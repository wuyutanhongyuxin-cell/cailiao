"""Stage 2B evidence package validator tests.

Honesty guard: the default aggregate is not ready, lists exactly the 7 evidence
groups and 5 blockers (lines 97/100/103/107/114) in order, and never checks a
ROADMAP parent. Group readiness is declared-metadata shape only.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_EVIDENCE_PACKAGE_VALIDATOR.md"
EXAMPLE_FILE = ROOT / "examples" / "stage2b_evidence_package.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

EXPECTED_GROUPS = [
    "declared_artifacts", "eval_run_manifest", "observability_snapshot", "release_dossier",
    "reproducibility_provenance", "risk_treatment", "industry_checklist",
]
EXPECTED_BLOCKER_IDS = [
    "real_query_set", "real_query_bm25_calibration",
    "real_embedding_provider_vector_store", "real_reranker_rrf", "real_nli_semantic_conflict",
]
EXPECTED_LINES = [97, 100, 103, 107, 114]


class EvidenceValidatorTest(unittest.TestCase):
    def test_method_and_default_not_ready(self):
        r = server.build_stage2b_evidence_package_validator()
        self.assertEqual(r["method"], "stage2b_evidence_package_validator_v1")
        self.assertFalse(r["ready_for_stage2b_completion"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_required_evidence_groups_exact_order(self):
        r = server.build_stage2b_evidence_package_validator()
        self.assertEqual(r["required_evidence_groups"], EXPECTED_GROUPS)

    def test_blocker_ids_exact_order(self):
        r = server.build_stage2b_evidence_package_validator()
        self.assertEqual(r["blocker_ids"], EXPECTED_BLOCKER_IDS)
        self.assertEqual([b["roadmap_line"] for b in r["blockers"]], EXPECTED_LINES)

    def test_each_blocker_maps_all_groups(self):
        r = server.build_stage2b_evidence_package_validator()
        for b in r["blockers"]:
            self.assertEqual(sorted(b["evidence_requirements"].keys()), sorted(EXPECTED_GROUPS))
            for g in EXPECTED_GROUPS:
                self.assertTrue(str(b["evidence_requirements"][g]).strip())

    def test_default_all_groups_missing_and_blockers_unsatisfied(self):
        r = server.build_stage2b_evidence_package_validator()
        self.assertEqual(sorted(r["outstanding_evidence_groups"]), sorted(EXPECTED_GROUPS))
        self.assertFalse(r["all_evidence_groups_ready"])
        self.assertFalse(r["all_blockers_satisfied"])
        for b in r["blockers"]:
            self.assertFalse(b["satisfied"])

    def test_evidence_group_ready_keys_match_groups(self):
        r = server.build_stage2b_evidence_package_validator()
        self.assertEqual(sorted(r["evidence_group_ready"].keys()), sorted(EXPECTED_GROUPS))
        self.assertTrue(all(v is False for v in r["evidence_group_ready"].values()))

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_evidence_package_validator(), ensure_ascii=False)


class DocExampleTest(unittest.TestCase):
    def test_doc_exists_and_lists_groups(self):
        self.assertTrue(DOC.exists())
        text = DOC.read_text(encoding="utf-8")
        for g in EXPECTED_GROUPS:
            self.assertIn(g, text)

    def test_example_is_template_and_not_ready(self):
        self.assertTrue(EXAMPLE_FILE.exists())
        data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        self.assertTrue(data.get("is_template"))
        self.assertFalse(data["ready_for_stage2b_completion"])
        self.assertFalse(data["roadmap_parent_items_checked"])
        self.assertEqual(data["required_evidence_groups"], EXPECTED_GROUPS)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_evidence_package_validator",
            ROOT / "tools" / "check_stage2b_evidence_package_validator.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)


if __name__ == "__main__":
    unittest.main()
