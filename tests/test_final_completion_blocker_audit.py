"""Final completion blocker audit tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "FINAL_COMPLETION_BLOCKER_AUDIT.md"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

EXPECTED_IDS = [
    "real_query_set",
    "real_query_bm25_calibration",
    "real_embedding_provider_vector_store",
    "real_reranker_rrf",
    "real_nli_semantic_conflict",
]
EXPECTED_LINES = [97, 100, 103, 107, 114]


class FinalCompletionBlockerAuditHelperTest(unittest.TestCase):
    def test_default_reports_project_blocked_by_external_input(self):
        r = server.build_final_completion_blocker_audit()
        self.assertEqual(r["method"], "final_completion_blocker_audit_v1")
        self.assertFalse(r["project_complete"])
        self.assertFalse(r["repo_only_work_remaining"])
        self.assertTrue(r["blocked_by_external_input"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_default_blockers_match_roadmap_lines(self):
        r = server.build_final_completion_blocker_audit()
        self.assertEqual([b["id"] for b in r["blockers"]], EXPECTED_IDS)
        self.assertEqual([b["roadmap_line"] for b in r["blockers"]], EXPECTED_LINES)
        self.assertEqual(r["open_external_blocker_ids"], EXPECTED_IDS)
        for blocker in r["blockers"]:
            self.assertEqual(blocker["status"], "blocked_by_external_input")

    def test_reuses_existing_audit_and_risk_register(self):
        r = server.build_final_completion_blocker_audit()
        self.assertEqual(r["external_dependency_audit_method"], "external_dependency_audit_v1")
        self.assertEqual(r["risk_register_method"], "stage2b_risk_register_v1")
        self.assertFalse(r["all_external_dependencies_satisfied"])
        self.assertFalse(r["all_risks_closed"])

    def test_json_serializable(self):
        json.dumps(server.build_final_completion_blocker_audit(), ensure_ascii=False)


class FinalCompletionBlockerAuditFilesTest(unittest.TestCase):
    def test_doc_exists_and_names_blockers(self):
        self.assertTrue(DOC.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)


class FinalCompletionBlockerAuditCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_final_completion_blocker_audit", ROOT / "tools" / "check_final_completion_blocker_audit.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)


if __name__ == "__main__":
    unittest.main()
