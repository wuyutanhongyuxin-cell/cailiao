import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class MaterialTaskStorageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_create_update_and_analyze_task(self):
        task = server.create_material_task({
            "title": "产业材料",
            "genre": "work_plan",
            "fields": {"目标": "提升"},
            "facts": "2026年完成3项试点，责任单位为区工信局。",
            "selected_evidence": [{"id": "e1", "text": "2026年完成3项试点。", "approved": True}],
            "approved_facts": [{"id": "f1", "text": "2026年完成3项试点。"}],
            "draft": "一、总体目标\n2026年完成3项试点。",
        })

        self.assertEqual(task["title"], "产业材料")
        self.assertEqual(task["fields"]["目标"], "提升")
        self.assertEqual(task["selected_evidence"][0]["id"], "e1")
        self.assertEqual(len(server.list_material_tasks()), 1)

        updated = server.update_material_task(task["id"], {
            "title": "产业材料（修订）",
            "fields": "not-json",
            "unknown": "ignored",
        })
        self.assertEqual(updated["title"], "产业材料（修订）")
        self.assertEqual(updated["fields"], {})
        self.assertNotIn("unknown", updated)

        result = server.analyze_material_task(task["id"])
        self.assertEqual(result["task"]["id"], task["id"])
        self.assertIn("status", result["analysis"])
        self.assertEqual(result["task"]["latest_analysis"]["status"], result["analysis"]["status"])


class MaterialTaskHTTPTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _url(self, path):
        return f"http://127.0.0.1:{self._port}{path}"

    def _json(self, method, path, body=None):
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self._url(path),
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with self._opener.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_task_http_flow(self):
        status, created = self._json("POST", "/api/tasks", {
            "title": "汇报材料",
            "genre": "work_report",
            "facts": "全年完成10项任务，责任单位为办公室。",
            "selected_evidence": [{"id": "ev1", "body": "全年完成10项任务。", "approved": True}],
        })
        self.assertEqual(status, 201)
        self.assertEqual(created["genre"], "work_report")

        status, listed = self._json("GET", "/api/tasks")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in listed["items"]], [created["id"]])

        status, updated = self._json("PUT", f"/api/tasks/{created['id']}", {
            "draft": "一、工作成效\n全年完成10项任务。",
            "locked_paragraphs": [0],
        })
        self.assertEqual(status, 200)
        self.assertEqual(updated["locked_paragraphs"], [0])

        status, analyzed = self._json("POST", f"/api/tasks/{created['id']}/analyze", {})
        self.assertEqual(status, 200)
        self.assertEqual(analyzed["task"]["id"], created["id"])
        self.assertIn("writing_state", analyzed["analysis"])

    def test_missing_task_returns_404(self):
        for method, path in (
            ("GET", "/api/tasks/missing"),
            ("PUT", "/api/tasks/missing"),
            ("POST", "/api/tasks/missing/analyze"),
        ):
            status, body = self._json(method, path, {})
            self.assertEqual(status, 404)
            self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
