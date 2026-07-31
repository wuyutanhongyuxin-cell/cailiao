import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class MaterialTaskFlowStorageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        self._orig_call_llm = server.call_llm
        self._orig_analyze_payload = server.analyze_payload
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.call_llm = self._orig_call_llm
        server.analyze_payload = self._orig_analyze_payload
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _ready_task(self):
        task = server.create_material_task({
            "title": "项目推进汇报",
            "genre": "work_report",
            "facts": "2026年推进3个重点项目，责任单位为办公室。",
            "draft": "一、工作成效\n2026年推进3个重点项目。",
        })
        server.attach_task_evidence(task["id"], {
            "id": "ev1",
            "title": "项目依据",
            "body": "2026年推进3个重点项目。",
        })
        server.approve_task_evidence(task["id"], evidence_ids=["ev1"])
        return server.get_material_task(task["id"])

    def test_blocked_generate_does_not_call_llm(self):
        task = server.create_material_task({"title": "空任务"})
        called = {"value": False}

        def fail_call_llm(prompt, config=None):
            called["value"] = True
            return {"mode": "llm", "draft": "should not happen"}

        server.call_llm = fail_call_llm
        result = server.generate_material_task(task["id"])

        self.assertEqual(result["mode"], "blocked")
        self.assertFalse(called["value"])
        self.assertEqual(result["draft"], "")
        self.assertTrue(result["task"]["latest_analysis"])

    def test_generate_saves_draft_and_version_when_llm_returns_draft(self):
        task = self._ready_task()

        def ready_analysis(payload):
            return {
                "genre": server.RULES["genres"]["work_report"],
                "status": "ready",
                "issues": [],
                "writing_state": {"state": "ready_to_draft", "can_export": False},
            }

        def fake_call_llm(prompt, config=None):
            return {"mode": "llm", "draft": "一、工作成效\n2026年推进3个重点项目。", "prompt": prompt}

        server.analyze_payload = ready_analysis
        server.call_llm = fake_call_llm
        result = server.generate_material_task(task["id"], {"model_mode": "prompt_only"})

        self.assertEqual(result["mode"], "llm")
        self.assertIn("2026年推进3个重点项目", result["task"]["draft"])
        self.assertEqual(len(result["task"]["draft_versions"]), 1)
        self.assertIn("writing_state", result)

    def test_audit_returns_counts_and_evidence_status(self):
        task = self._ready_task()
        result = server.audit_material_task(task["id"])

        audit = result["audit"]
        self.assertEqual(audit["method"], "material_task_audit_v1")
        self.assertGreaterEqual(audit["evidence_status"]["approved_facts"], 1)
        self.assertIn("can_export", audit)
        self.assertIn("repair_unit_count", audit)

    def test_preflight_saves_export_artifact(self):
        task = self._ready_task()
        result = server.build_material_task_export_preflight(task["id"])

        self.assertEqual(result["artifact"]["kind"], "docx_preflight")
        self.assertIn("summary", result["preflight"])
        saved = server.get_material_task(task["id"])
        self.assertEqual(saved["export_artifacts"][-1]["kind"], "docx_preflight")

    def test_docx_export_is_valid_zip(self):
        task = self._ready_task()
        raw = server.export_material_task_docx(task["id"])

        self.assertIsInstance(raw, bytes)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            with zipfile.ZipFile(tmp_path) as zf:
                self.assertIn("word/document.xml", zf.namelist())
        finally:
            tmp_path.unlink(missing_ok=True)


class MaterialTaskFlowHTTPTest(unittest.TestCase):
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

    def test_http_flow_routes(self):
        status, task = self._json("POST", "/api/tasks", {
            "title": "项目推进汇报",
            "genre": "work_report",
            "facts": "2026年推进3个重点项目，责任单位为办公室。",
            "draft": "一、工作成效\n2026年推进3个重点项目。",
        })
        self.assertEqual(status, 201)

        status, generated = self._json("POST", f"/api/tasks/{task['id']}/generate", {
            "config": {"model_mode": "prompt_only"},
        })
        self.assertEqual(status, 200)
        self.assertIn("analysis", generated)

        status, audit = self._json("POST", f"/api/tasks/{task['id']}/audit", {})
        self.assertEqual(status, 200)
        self.assertEqual(audit["audit"]["method"], "material_task_audit_v1")

        status, preflight = self._json("POST", f"/api/tasks/{task['id']}/export/preflight", {})
        self.assertEqual(status, 200)
        self.assertIn("preflight", preflight)

        req = urllib.request.Request(
            self._url(f"/api/tasks/{task['id']}/export/docx"),
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._opener.open(req, timeout=10) as resp:
            raw = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertIn("wordprocessingml.document", resp.headers.get("Content-Type"))
            self.assertGreater(len(raw), 100)

    def test_missing_task_and_bad_body(self):
        status, body = self._json("POST", "/api/tasks/missing/audit", {})
        self.assertEqual(status, 404)
        self.assertIn("error", body)

        status, task = self._json("POST", "/api/tasks", {"title": "坏请求"})
        self.assertEqual(status, 201)
        status, body = self._json("POST", f"/api/tasks/{task['id']}/generate", {"config": []})
        self.assertEqual(status, 422)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
