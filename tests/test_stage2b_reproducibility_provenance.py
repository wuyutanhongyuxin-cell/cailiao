"""Stage 2B reproducibility/provenance checklist tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_REPRODUCIBILITY_PROVENANCE.md"
EXAMPLE = ROOT / "examples" / "stage2b_reproducibility_provenance.example.json"

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


class ReproducibilityProvenanceHelperTest(unittest.TestCase):
    def test_default_entries_match_stage2b_blockers(self):
        r = server.build_stage2b_reproducibility_provenance()
        self.assertEqual(r["method"], "stage2b_reproducibility_provenance_v1")
        self.assertEqual(r["entry_count"], 5)
        self.assertEqual([e["id"] for e in r["entries"]], EXPECTED_IDS)
        self.assertEqual([e["roadmap_line"] for e in r["entries"]], EXPECTED_LINES)
        self.assertEqual(r["missing_reproducibility_ids"], EXPECTED_IDS)
        self.assertFalse(r["reproducibility_ready"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_each_entry_has_provenance_fields(self):
        r = server.build_stage2b_reproducibility_provenance()
        for entry in r["entries"]:
            self.assertTrue(entry["provenance_entities"])
            self.assertTrue(entry["provenance_activities"])
            self.assertTrue(entry["provenance_agents"])
            self.assertTrue(entry["reproducibility_artifacts"])
            self.assertTrue(entry["immutability_requirements"])
            self.assertEqual(entry["status"], "missing_reproducibility_proof")

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_reproducibility_provenance(), ensure_ascii=False)


class ReproducibilityProvenanceFilesTest(unittest.TestCase):
    def test_doc_and_example_exist(self):
        self.assertTrue(DOC.exists())
        self.assertTrue(EXAMPLE.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertTrue(data["is_template"])
        self.assertEqual(data["artifacts"], {})


class ReproducibilityProvenanceCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_reproducibility_provenance",
            ROOT / "tools" / "check_stage2b_reproducibility_provenance.py")
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
