"""Stage 2B standards traceability tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_STANDARDS_TRACEABILITY.md"
EXAMPLE = ROOT / "examples" / "stage2b_standards_traceability.example.json"

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


class StandardsTraceabilityHelperTest(unittest.TestCase):
    def test_default_rows_match_stage2b_blockers(self):
        r = server.build_stage2b_standards_traceability()
        self.assertEqual(r["method"], "stage2b_standards_traceability_v1")
        self.assertEqual(r["row_count"], 5)
        self.assertEqual([row["id"] for row in r["rows"]], EXPECTED_IDS)
        self.assertEqual([row["roadmap_line"] for row in r["rows"]], EXPECTED_LINES)
        self.assertEqual(r["outstanding_ids"], EXPECTED_IDS)
        self.assertFalse(r["all_external_proofs_present"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_each_row_has_references_and_proof_fields(self):
        r = server.build_stage2b_standards_traceability()
        for row in r["rows"]:
            self.assertTrue(row["standard_or_reference"])
            self.assertTrue(row["required_evidence_artifacts"])
            self.assertTrue(row["repo_guardrails"])
            self.assertTrue(row["remaining_real_world_proof"])
            self.assertEqual(row["status"], "aligned_but_needs_external_input")
            for ref in row["standard_or_reference"]:
                self.assertIn("name", ref)
                self.assertIn("url", ref)
                self.assertTrue(ref["url"].startswith("https://"))

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_standards_traceability(), ensure_ascii=False)


class StandardsTraceabilityFilesTest(unittest.TestCase):
    def test_doc_and_example_exist(self):
        self.assertTrue(DOC.exists())
        self.assertTrue(EXAMPLE.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertTrue(data["is_template"])
        self.assertEqual(data["artifacts"], {})


class StandardsTraceabilityCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_standards_traceability",
            ROOT / "tools" / "check_stage2b_standards_traceability.py")
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
