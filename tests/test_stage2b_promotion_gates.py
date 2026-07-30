"""Stage 2B promotion gate tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_PROMOTION_GATES.md"
EXAMPLE = ROOT / "examples" / "stage2b_promotion_gates.example.json"

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


class PromotionGatesHelperTest(unittest.TestCase):
    def test_default_gates_match_stage2b_blockers(self):
        r = server.build_stage2b_promotion_gates()
        self.assertEqual(r["method"], "stage2b_promotion_gates_v1")
        self.assertEqual(r["gate_count"], 5)
        self.assertEqual([g["id"] for g in r["gates"]], EXPECTED_IDS)
        self.assertEqual([g["roadmap_line"] for g in r["gates"]], EXPECTED_LINES)
        self.assertEqual(r["blocked_ids"], EXPECTED_IDS)
        self.assertFalse(r["ready_for_promotion"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_each_gate_has_policy_fields(self):
        r = server.build_stage2b_promotion_gates()
        for gate in r["gates"]:
            self.assertTrue(gate["required_metrics_or_artifacts"])
            self.assertTrue(gate["default_gate"])
            self.assertTrue(gate["rollback_trigger"])
            self.assertTrue(gate["evidence_source"])
            self.assertTrue(gate["repo_guardrails"])
            self.assertEqual(gate["current_status"], "blocked_by_external_input")

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_promotion_gates(), ensure_ascii=False)


class PromotionGatesFilesTest(unittest.TestCase):
    def test_doc_and_example_exist(self):
        self.assertTrue(DOC.exists())
        self.assertTrue(EXAMPLE.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertTrue(data["is_template"])
        self.assertEqual(data["artifacts"], {})


class PromotionGatesCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_promotion_gates", ROOT / "tools" / "check_stage2b_promotion_gates.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_template_example_exits_one(self):
        self.assertEqual(self._run(["--config", str(EXAMPLE), "--json"]), 1)


if __name__ == "__main__":
    unittest.main()
