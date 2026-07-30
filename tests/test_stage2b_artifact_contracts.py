"""Stage 2B artifact-contract template tests.

Honesty guard: contracts describe declared shape only. The placeholder example must
NOT make the external audit fully satisfied by default, and nothing here checks a
ROADMAP parent item.
"""

import contextlib
import importlib.util
import io
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
CONTRACTS_DOC = ROOT / "docs" / "STAGE2B_ARTIFACT_CONTRACTS.md"
EXAMPLE_FILE = ROOT / "examples" / "stage2b_artifacts.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

EXPECTED_KEYS = [
    "real_query_set", "real_query_bm25_calibration",
    "real_embedding_provider_vector_store", "real_reranker_rrf", "real_nli_semantic_conflict",
]

# Secret-shaped value patterns that must NOT appear in the example (env var NAMES ok).
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_.]{16,}"),
]


class ContractsHelperTest(unittest.TestCase):
    def test_contract_count_and_keys_match_blockers(self):
        r = server.build_stage2b_artifact_contracts()
        self.assertEqual(r["contract_count"], 5)
        self.assertEqual([c["key"] for c in r["contracts"]], EXPECTED_KEYS)
        audit_ids = [b["id"] for b in server.build_external_dependency_audit()["blockers"]]
        self.assertEqual([c["key"] for c in r["contracts"]], audit_ids)

    def test_each_contract_has_required_documentation_fields(self):
        r = server.build_stage2b_artifact_contracts()
        for c in r["contracts"]:
            for field in ("key", "required_fields", "forbidden_fields", "validator",
                          "proves", "does_not_prove", "reference_url"):
                self.assertIn(field, c)
            self.assertTrue(c["reference_url"])

    def test_default_reports_placeholder_only_and_no_parent_check(self):
        r = server.build_stage2b_artifact_contracts()
        self.assertTrue(r["example_is_placeholder_only"])
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertEqual(r["ready_artifact_count"], 0)

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_artifact_contracts(), ensure_ascii=False)


class ExampleFileTest(unittest.TestCase):
    def test_example_exists_and_parses(self):
        self.assertTrue(EXAMPLE_FILE.exists())
        data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        self.assertIn("artifacts", data)

    def test_example_has_no_secret_shaped_values(self):
        text = EXAMPLE_FILE.read_text(encoding="utf-8")
        for pat in _SECRET_VALUE_PATTERNS:
            self.assertIsNone(pat.search(text), f"example contains a secret-shaped value: {pat.pattern}")

    def test_example_does_not_make_external_audit_satisfied(self):
        data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        audit = server.build_external_dependency_audit(data)
        # Placeholder query sets can never be ready_real, so the aggregate must be false.
        self.assertFalse(audit["all_external_dependencies_satisfied"])
        self.assertNotIn("real_query_set", audit["satisfied_ids"])
        self.assertNotIn("real_query_bm25_calibration", audit["satisfied_ids"])

    def test_example_contracts_ready_count_below_five(self):
        data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        r = server.build_stage2b_artifact_contracts(data)
        self.assertLess(r["ready_artifact_count"], 5)


class ContractsDocTest(unittest.TestCase):
    def test_doc_exists_and_documents_all_keys(self):
        self.assertTrue(CONTRACTS_DOC.exists())
        text = CONTRACTS_DOC.read_text(encoding="utf-8")
        for key in EXPECTED_KEYS:
            self.assertIn(key, text)

    def test_doc_states_shape_only_boundary(self):
        text = CONTRACTS_DOC.read_text(encoding="utf-8").lower()
        self.assertIn("shape", text)
        self.assertIn("not", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_artifact_contracts", ROOT / "tools" / "check_stage2b_artifact_contracts.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_zero(self):
        self.assertEqual(self._run(["--json"]), 0)

    def test_cli_require_ready_artifacts_exits_one_by_default(self):
        self.assertEqual(self._run(["--require-ready-artifacts", "--json"]), 1)

    def test_cli_require_ready_with_placeholder_example_still_exits_one(self):
        self.assertEqual(
            self._run(["--config", str(EXAMPLE_FILE), "--require-ready-artifacts", "--json"]), 1)


if __name__ == "__main__":
    unittest.main()
