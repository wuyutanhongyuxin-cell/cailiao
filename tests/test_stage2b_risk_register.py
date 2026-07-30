"""Stage 2B risk register tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_RISK_REGISTER.md"
EXAMPLE = ROOT / "examples" / "stage2b_risk_register.example.json"

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


class RiskRegisterHelperTest(unittest.TestCase):
    def test_default_risks_match_stage2b_blockers(self):
        r = server.build_stage2b_risk_register()
        self.assertEqual(r["method"], "stage2b_risk_register_v1")
        self.assertEqual(r["risk_count"], 5)
        self.assertEqual([risk["id"] for risk in r["risks"]], EXPECTED_IDS)
        self.assertEqual([risk["roadmap_line"] for risk in r["risks"]], EXPECTED_LINES)
        self.assertEqual(r["open_risk_ids"], EXPECTED_IDS)
        self.assertFalse(r["all_risks_closed"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_each_risk_has_treatment_fields(self):
        r = server.build_stage2b_risk_register()
        for risk in r["risks"]:
            self.assertTrue(risk["risk_statement"])
            self.assertTrue(risk["impact"])
            self.assertTrue(risk["likelihood_default"])
            self.assertTrue(risk["severity_default"])
            self.assertTrue(risk["treatment_plan"])
            self.assertTrue(risk["owner_role"])
            self.assertTrue(risk["evidence_to_close"])
            self.assertEqual(risk["status"], "open_external_risk")

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_risk_register(), ensure_ascii=False)


class RiskRegisterFilesTest(unittest.TestCase):
    def test_doc_and_example_exist(self):
        self.assertTrue(DOC.exists())
        self.assertTrue(EXAMPLE.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertTrue(data["is_template"])
        self.assertEqual(data["artifacts"], {})


class RiskRegisterCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_risk_register", ROOT / "tools" / "check_stage2b_risk_register.py")
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
