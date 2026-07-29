"""Stage 5 benchmark suite schema + deterministic scoring skeleton tests."""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
SAMPLE = ROOT / "tests" / "data" / "benchmark_suite_sample.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _load_sample():
    return server.load_benchmark_suite(SAMPLE)


class BenchmarkSuiteValidationTest(unittest.TestCase):
    def test_sample_suite_validates(self):
        report = server.validate_benchmark_suite(_load_sample())
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["case_count"], 3)
        self.assertIn("通知", report["genres"])
        # Placeholder + small suite should warn but not fail.
        self.assertTrue(report["warnings"])

    def test_expected_element_counts_sum_markers(self):
        report = server.validate_benchmark_suite(_load_sample())
        counts = report["expected_element_counts"]
        for dim in server.BENCHMARK_DIMENSIONS:
            self.assertIn(dim, counts)
            self.assertGreater(counts[dim], 0)

    def test_missing_anonymized_flag_fails(self):
        suite = _load_sample()
        suite["metadata"].pop("anonymized", None)
        report = server.validate_benchmark_suite(suite)
        self.assertFalse(report["passed"])
        self.assertTrue(any("anonymized" in e for e in report["errors"]))

    def test_duplicate_case_id_fails(self):
        suite = _load_sample()
        suite["cases"][1]["id"] = suite["cases"][0]["id"]
        report = server.validate_benchmark_suite(suite)
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate id" in e for e in report["errors"]))

    def test_unknown_expected_dimension_fails(self):
        suite = _load_sample()
        suite["cases"][0]["expected_elements"]["tone"] = ["严肃"]
        report = server.validate_benchmark_suite(suite)
        self.assertFalse(report["passed"])
        self.assertTrue(any("unknown expected_elements" in e for e in report["errors"]))

    def test_case_without_any_expected_dimension_fails(self):
        suite = _load_sample()
        suite["cases"][0]["expected_elements"] = {}
        report = server.validate_benchmark_suite(suite)
        self.assertFalse(report["passed"])
        self.assertTrue(any("at least one non-empty dimension" in e for e in report["errors"]))

    def test_non_object_suite_fails_safely(self):
        report = server.validate_benchmark_suite(["not", "an", "object"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["case_count"], 0)

    def test_load_missing_cases_raises(self):
        # A JSON object without a 'cases' list must raise on load.
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"metadata": {}}, f)
            path = f.name
        with self.assertRaises(ValueError):
            server.load_benchmark_suite(path)


class BenchmarkScoringTest(unittest.TestCase):
    def test_reference_answers_score_perfectly(self):
        # The fixture's reference answers are authored to contain every marker.
        result = server.score_benchmark_suite(_load_sample())
        self.assertEqual(result["method"], "benchmark_lexical_scoring_v1")
        self.assertEqual(result["case_count"], 3)
        self.assertEqual(result["aggregate"]["overall"], 1.0)
        for dim in server.BENCHMARK_DIMENSIONS:
            self.assertEqual(result["aggregate"]["dimensions"][dim], 1.0)

    def test_empty_response_scores_zero(self):
        suite = _load_sample()
        responses = {case["id"]: "" for case in suite["cases"]}
        result = server.score_benchmark_suite(suite, responses)
        self.assertEqual(result["aggregate"]["overall"], 0.0)
        for case in result["cases"]:
            self.assertFalse(case["has_candidate"])
            self.assertEqual(case["overall"], 0.0)

    def test_partial_coverage_is_proportional(self):
        suite = _load_sample()
        first = suite["cases"][0]
        markers = first["expected_elements"]["facts"]
        # A response containing exactly one of two facts markers -> 0.5 on facts.
        responses = {first["id"]: markers[0]}
        result = server.score_benchmark_suite(suite, responses)
        scored = next(c for c in result["cases"] if c["id"] == first["id"])
        self.assertEqual(scored["dimensions"]["facts"]["score"], 0.5)
        self.assertEqual(scored["dimensions"]["facts"]["matched"], 1)
        self.assertIn(markers[1], scored["dimensions"]["facts"]["missing"])

    def test_scoring_is_json_serializable(self):
        json.dumps(server.score_benchmark_suite(_load_sample()), ensure_ascii=False)

    def test_scoring_is_deterministic(self):
        suite = _load_sample()
        a = server.score_benchmark_suite(copy.deepcopy(suite))
        b = server.score_benchmark_suite(copy.deepcopy(suite))
        self.assertEqual(a, b)


class BenchmarkCliTest(unittest.TestCase):
    def test_cli_validate_exit_zero(self):
        spec_cli = importlib.util.spec_from_file_location(
            "validate_benchmark_suite", ROOT / "tools" / "validate_benchmark_suite.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        # Suppress the tool's JSON stdout so it does not clutter the test run.
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--suite", str(SAMPLE), "--json", "--score"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
