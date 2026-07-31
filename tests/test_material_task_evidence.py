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


class MaterialTaskEvidenceStorageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _seed_task_and_document(self):
        task = server.create_material_task({
            "title": "招商工作方案",
            "genre": "work_plan",
            "fields": {"目标": "产业链招商"},
            "facts": "2026年推进3个重点项目，责任单位为招商主管部门。",
        })
        imported = server.import_document({
            "title": "产业链招商政策",
            "format": "txt",
            "text": "2026年推进3个重点项目。招商主管部门负责产业链招商。",
            "source_url": "https://example.gov.cn/policy",
            "source_type": "local_government",
            "status": "有效",
        })
        self.assertIn(imported["status"], {"succeeded", "new_version"})
        return task

    def test_search_derives_query_and_does_not_mutate_task(self):
        task = self._seed_task_and_document()
        result = server.search_task_evidence(task["id"], limit=5)

        self.assertEqual(result["task_id"], task["id"])
        self.assertIn("招商工作方案", result["query"])
        self.assertTrue(result["items"])
        self.assertEqual(server.get_material_task(task["id"])["selected_evidence"], [])

    def test_attach_deduplicates_and_persists(self):
        task = self._seed_task_and_document()
        item = {
            "id": "ev1",
            "title": "政策依据",
            "body": "2026年推进3个重点项目。",
            "source": "政策库",
        }
        result = server.attach_task_evidence(task["id"], [item, dict(item)])

        selected = result["task"]["selected_evidence"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["id"], "ev1")
        self.assertFalse(selected[0]["approved"])
        self.assertEqual(result["evidence_status"]["selected"], 1)

    def test_approve_by_id_promotes_approved_fact(self):
        task = self._seed_task_and_document()
        server.attach_task_evidence(task["id"], {
            "id": "ev1",
            "title": "政策依据",
            "body": "2026年推进3个重点项目。",
        })
        result = server.approve_task_evidence(task["id"], evidence_ids=["ev1"])

        selected = result["task"]["selected_evidence"]
        self.assertTrue(selected[0]["approved"])
        self.assertEqual(result["evidence_status"]["approved_evidence"], 1)
        self.assertEqual(result["evidence_status"]["approved_facts"], 1)
        self.assertEqual(result["task"]["approved_facts"][0]["source_evidence_id"], "ev1")

    def test_explicit_approved_facts_deduplicate(self):
        task = self._seed_task_and_document()
        result = server.approve_task_evidence(task["id"], approved_facts=[
            {"id": "manual", "text": "责任单位为招商主管部门。"},
            {"id": "manual", "text": "责任单位为招商主管部门。"},
        ])

        self.assertEqual(len(result["task"]["approved_facts"]), 1)
        self.assertEqual(result["evidence_status"]["approved_facts"], 1)

    def test_analyze_sees_approved_evidence(self):
        task = self._seed_task_and_document()
        server.attach_task_evidence(task["id"], {
            "id": "ev1",
            "title": "政策依据",
            "body": "2026年推进3个重点项目。",
        })
        server.approve_task_evidence(task["id"], evidence_ids=["ev1"])
        analyzed = server.analyze_material_task(task["id"])

        self.assertEqual(analyzed["task"]["id"], task["id"])
        self.assertEqual(analyzed["task"]["approved_facts"][0]["source_evidence_id"], "ev1")
        self.assertIn("approved_facts_audit", analyzed["analysis"])


class MaterialTaskEvidenceHTTPTest(unittest.TestCase):
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

    def test_http_evidence_flow(self):
        status, task = self._json("POST", "/api/tasks", {
            "title": "汇报材料",
            "facts": "2026年推进3个重点项目。",
        })
        self.assertEqual(status, 201)
        status, attached = self._json("POST", f"/api/tasks/{task['id']}/evidence/attach", {
            "items": [{"id": "ev1", "title": "政策依据", "body": "2026年推进3个重点项目。"}],
        })
        self.assertEqual(status, 200)
        self.assertEqual(attached["evidence_status"]["selected"], 1)

        status, approved = self._json("POST", f"/api/tasks/{task['id']}/evidence/approve", {
            "evidence_ids": ["ev1"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(approved["evidence_status"]["approved_facts"], 1)

        status, evidence_status = self._json("GET", f"/api/tasks/{task['id']}/evidence/status")
        self.assertEqual(status, 200)
        self.assertTrue(evidence_status["ready_for_analysis"])

    def test_http_missing_task_and_bad_body(self):
        status, body = self._json("POST", "/api/tasks/missing/evidence/attach", {
            "items": [{"id": "ev1", "body": "x"}],
        })
        self.assertEqual(status, 404)
        self.assertIn("error", body)

        status, task = self._json("POST", "/api/tasks", {"title": "坏请求"})
        self.assertEqual(status, 201)
        status, body = self._json("POST", f"/api/tasks/{task['id']}/evidence/approve", {
            "evidence_ids": "ev1",
        })
        self.assertEqual(status, 422)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
