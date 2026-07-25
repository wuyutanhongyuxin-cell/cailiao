import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

CLI = ROOT / "tools" / "validate_retrieval_suite.py"
cli_spec = importlib.util.spec_from_file_location("validate_retrieval_suite", CLI)
cli = importlib.util.module_from_spec(cli_spec)
cli_spec.loader.exec_module(cli)

SUITE_PATH = ROOT / "tests" / "data" / "retrieval_eval_suite.json"


def _valid_case(cid="c1", **over):
    case = {"id": cid, "query": "alpha 30 2026",
            "filters": {"effective_only": "true"},
            "relevant_titles": ["Alpha Policy"]}
    case.update(over)
    return case


def _suite(cases, **over):
    s = {"suite": "unit-test-suite", "cases": cases}
    s.update(over)
    return s


class ValidateRetrievalSuiteTest(unittest.TestCase):
    def test_valid_suite_passes(self):
        report = server.validate_retrieval_suite(_suite([_valid_case()]))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["case_count"], 1)
        self.assertIn("effective_only", report["filter_keys_used"])
        self.assertEqual(report["relevance_target_counts"]["relevant_titles"], 1)

    def test_non_object_suite_fails(self):
        report = server.validate_retrieval_suite([1, 2, 3])
        self.assertFalse(report["passed"])
        self.assertTrue(report["errors"])

    def test_empty_cases_fails(self):
        report = server.validate_retrieval_suite(_suite([]))
        self.assertFalse(report["passed"])

    def test_duplicate_ids_fail(self):
        report = server.validate_retrieval_suite(_suite([_valid_case("dup"), _valid_case("dup")]))
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate id" in e for e in report["errors"]))

    def test_missing_query_fails(self):
        report = server.validate_retrieval_suite(_suite([_valid_case(query="  ")]))
        self.assertFalse(report["passed"])
        self.assertTrue(any("query" in e for e in report["errors"]))

    def test_unsupported_filter_key_fails(self):
        report = server.validate_retrieval_suite(
            _suite([_valid_case(filters={"effective_only": "true", "bogus_key": "x"})]))
        self.assertFalse(report["passed"])
        self.assertTrue(any("unsupported filter key" in e for e in report["errors"]))

    def test_missing_relevance_target_fails(self):
        case = {"id": "c1", "query": "q", "filters": {}}
        report = server.validate_retrieval_suite(_suite([case]))
        self.assertFalse(report["passed"])
        self.assertTrue(any("relevance target" in e for e in report["errors"]))

    def test_empty_string_relevance_target_fails(self):
        report = server.validate_retrieval_suite(_suite([_valid_case(relevant_titles=["ok", "  "])]))
        self.assertFalse(report["passed"])
        self.assertTrue(any("non-empty strings" in e for e in report["errors"]))

    def test_non_int_min_authority_fails(self):
        report = server.validate_retrieval_suite(
            _suite([_valid_case(filters={"min_authority": "high"})]))
        self.assertFalse(report["passed"])
        self.assertTrue(any("min_authority" in e for e in report["errors"]))

    def test_unknown_format_warns_not_fails(self):
        report = server.validate_retrieval_suite(
            _suite([_valid_case(filters={"format": "pptx"})]))
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("format" in w for w in report["warnings"]))

    def test_small_suite_warns(self):
        report = server.validate_retrieval_suite(_suite([_valid_case()]))
        self.assertTrue(any("< 50" in w for w in report["warnings"]))

    def test_placeholder_metadata_warns(self):
        report = server.validate_retrieval_suite(
            _suite([_valid_case()], description="synthetic placeholder set"))
        self.assertTrue(report["passed"])
        self.assertTrue(any("placeholder" in w for w in report["warnings"]))

    def test_markers_only_is_a_valid_target(self):
        case = {"id": "m1", "query": "q", "filters": {},
                "relevant_chunk_markers": ["some marker text"]}
        report = server.validate_retrieval_suite(_suite([case]))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["relevance_target_counts"]["relevant_chunk_markers"], 1)

    def test_shipped_placeholder_suite_passes_with_warnings(self):
        suite = server.load_retrieval_eval_suite(SUITE_PATH)
        report = server.validate_retrieval_suite(suite)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["case_count"], 10)
        self.assertTrue(report["warnings"])  # <50 and/or placeholder


class ValidateSuiteCliTest(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        return code, buf.getvalue()

    def test_cli_pass_on_shipped_suite(self):
        code, out = self._run(["--suite", str(SUITE_PATH), "--json"])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["passed"])
        self.assertEqual(report["case_count"], 10)

    def test_cli_nonzero_on_missing_file(self):
        code, out = self._run(["--suite", str(SUITE_PATH) + ".nope.json", "--json"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["passed"])

    def test_cli_nonzero_on_invalid_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "invalid_suite.json"
            bad.write_text(json.dumps({"suite": "x", "cases": [{"id": "a", "query": ""}]}),
                           encoding="utf-8")
            code, out = self._run(["--suite", str(bad), "--json"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["passed"])


if __name__ == "__main__":
    unittest.main()
