"""Stage 3 deterministic writing-state machine (v1) tests.

Covers all five workflow states, that an approval flag can never override a
blocker/failure, that analyze_payload always includes writing_state, and that
the /api/generate blocked response includes writing_state. Deterministic and
standard-library only; no network / LLM (the blocked path never calls call_llm).

The writing state is a deterministic workflow surface, NOT semantic review and
NOT DOCX formatting.
"""

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _fields(genre):
    return {name: "已填" for name in server.RULES["genres"][genre]["required_fields"]}


# A single-paragraph draft with owner+time+result (passes the action guard) and
# no policy/year/percentage/quantity pattern -> no fail issues.
CLEAN_DRAFT = "由办公室牵头，年底前完成台账建立并形成长效机制。"
# A vague-phrase draft with no action guard -> a fail issue.
VAGUE_DRAFT = "总体要求\n要加强组织领导，形成工作合力，确保取得实效。"


class WritingStateMachineTest(unittest.TestCase):
    """All five deterministic states + approval-cannot-override guarantees."""

    def test_analyze_payload_always_includes_writing_state(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        self.assertIn("writing_state", res)
        ws = res["writing_state"]
        self.assertEqual(ws["method"], "deterministic_writing_state_v1")
        for key in ("state", "label", "can_generate", "can_export", "blockers",
                    "failures", "warnings", "required_actions"):
            self.assertIn(key, ws)

    def test_materials_insufficient(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        ws = res["writing_state"]
        self.assertEqual(res["status"], "blocked")
        self.assertEqual(ws["state"], "materials_insufficient")
        self.assertEqual(ws["label"], "资料不足")
        self.assertFalse(ws["can_generate"])
        self.assertFalse(ws["can_export"])
        self.assertTrue(ws["blockers"])
        self.assertTrue(any(a["code"].startswith("fill_") for a in ws["required_actions"]))

    def test_ready_to_draft(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": _fields("work_plan"),
                                      "facts": "会议要求推进相关工作"})
        ws = res["writing_state"]
        self.assertEqual(ws["state"], "ready_to_draft")
        self.assertEqual(ws["label"], "可起草")
        self.assertTrue(ws["can_generate"])
        self.assertFalse(ws["can_export"])
        self.assertFalse(ws["draft_present"])
        self.assertTrue(any(a["code"] == "generate_draft" for a in ws["required_actions"]))

    def test_needs_revision(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": _fields("work_plan"),
                                      "facts": "会议要求推进相关工作", "draft": VAGUE_DRAFT})
        ws = res["writing_state"]
        self.assertEqual(res["status"], "fail")
        self.assertEqual(ws["state"], "needs_revision")
        self.assertEqual(ws["label"], "待修")
        self.assertTrue(ws["can_generate"])
        self.assertFalse(ws["can_export"])
        self.assertTrue(ws["failures"])
        self.assertTrue(any(a["code"].startswith("fix_") for a in ws["required_actions"]))

    def test_ready_for_review(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": _fields("work_plan"),
                                      "facts": "会议要求推进相关工作", "draft": CLEAN_DRAFT})
        ws = res["writing_state"]
        self.assertNotEqual(res["status"], "blocked")
        self.assertEqual(ws["failures"], [])
        self.assertEqual(ws["state"], "ready_for_review")
        self.assertEqual(ws["label"], "待审")
        self.assertTrue(ws["can_generate"])
        self.assertFalse(ws["can_export"])
        self.assertTrue(ws["draft_present"])
        self.assertTrue(any(a["code"] == "human_review" for a in ws["required_actions"]))

    def test_ready_to_export_when_approved(self):
        base = {"genre": "work_plan", "fields": _fields("work_plan"),
                "facts": "会议要求推进相关工作", "draft": CLEAN_DRAFT}
        for approve_key in ("review_approved", "approved"):
            payload = dict(base)
            payload[approve_key] = True
            ws = server.analyze_payload(payload)["writing_state"]
            self.assertEqual(ws["state"], "ready_to_export", f"via {approve_key}")
            self.assertEqual(ws["label"], "可导出")
            self.assertTrue(ws["can_generate"])
            self.assertTrue(ws["can_export"])
            self.assertTrue(any(a["code"] == "export_docx" for a in ws["required_actions"]))

    def test_approved_cannot_override_blockers(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": "",
                                      "review_approved": True, "approved": True})
        ws = res["writing_state"]
        self.assertEqual(ws["state"], "materials_insufficient")
        self.assertFalse(ws["can_export"])

    def test_approved_cannot_override_failures(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": _fields("work_plan"),
                                      "facts": "会议要求推进相关工作", "draft": VAGUE_DRAFT,
                                      "review_approved": True})
        ws = res["writing_state"]
        self.assertEqual(ws["state"], "needs_revision")
        self.assertFalse(ws["can_export"])

    def test_writing_state_is_json_serializable(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        # Round-trips without error.
        json.dumps(res["writing_state"])


class WritingStateGenerateHTTPTest(unittest.TestCase):
    """/api/generate responses carry writing_state (blocked path, no LLM)."""

    def setUp(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self._port}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self._opener.open(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_generate_blocked_includes_writing_state(self):
        status, data = self._post("/api/generate", {"genre": "work_plan", "fields": {}, "facts": ""})
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "blocked")
        # Top-level and nested-in-analysis writing_state both present and consistent.
        self.assertIn("writing_state", data)
        self.assertEqual(data["writing_state"]["state"], "materials_insufficient")
        self.assertFalse(data["writing_state"]["can_generate"])
        self.assertEqual(data["analysis"]["writing_state"]["state"], "materials_insufficient")


if __name__ == "__main__":
    unittest.main()
