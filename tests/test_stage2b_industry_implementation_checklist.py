"""Stage 2B industry implementation checklist tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_INDUSTRY_IMPLEMENTATION_CHECKLIST.md"

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


class Stage2BIndustryChecklistHelperTest(unittest.TestCase):
    def test_default_sections_match_stage2b_blockers(self):
        r = server.build_stage2b_industry_implementation_checklist()
        self.assertEqual(r["method"], "stage2b_industry_implementation_checklist_v1")
        self.assertEqual(r["checklist_count"], 5)
        self.assertEqual([s["id"] for s in r["sections"]], EXPECTED_IDS)
        self.assertEqual([s["roadmap_line"] for s in r["sections"]], EXPECTED_LINES)
        self.assertEqual(r["outstanding_ids"], EXPECTED_IDS)
        self.assertFalse(r["ready_for_stage2b_completion"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_each_section_has_quality_controls_and_https_refs(self):
        r = server.build_stage2b_industry_implementation_checklist()
        for section in r["sections"]:
            self.assertTrue(section["industry_references"])
            self.assertTrue(section["implementation_steps"])
            self.assertTrue(section["minimum_evidence"])
            self.assertTrue(section["quality_gates"])
            self.assertTrue(section["observability_requirements"])
            self.assertTrue(section["rollback_or_human_review"])
            self.assertEqual(section["current_status"], "blocked_by_external_input")
            for ref in section["industry_references"]:
                self.assertIn("name", ref)
                self.assertIn("url", ref)
                self.assertIn("relevance", ref)
                self.assertTrue(ref["url"].startswith("https://"))

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_industry_implementation_checklist(), ensure_ascii=False)


class Stage2BIndustryChecklistFilesTest(unittest.TestCase):
    def test_doc_exists_and_names_references(self):
        self.assertTrue(DOC.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)
        for marker in ("NIST", "BEIR", "Qdrant", "SentenceTransformers", "OpenTelemetry", "FEVER", "SNLI"):
            self.assertIn(marker, text)


class Stage2BIndustryChecklistCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_industry_implementation_checklist",
            ROOT / "tools" / "check_stage2b_industry_implementation_checklist.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)


if __name__ == "__main__":
    unittest.main()
