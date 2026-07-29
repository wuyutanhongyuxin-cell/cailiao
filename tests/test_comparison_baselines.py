"""Stage 5 comparison-baseline matrix + aggregate skeleton tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
SUITE = ROOT / "tests" / "data" / "benchmark_suite_sample.json"
OUTPUTS = ROOT / "tests" / "data" / "comparison_baselines_sample.json"
SCORES = ROOT / "tests" / "data" / "comparison_baseline_scores_sample.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _load(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


class ComparisonMatrixTest(unittest.TestCase):
    def setUp(self):
        self.suite = server.load_benchmark_suite(SUITE)
        self.outputs = _load(OUTPUTS)

    def test_matrix_joins_arms_to_cases(self):
        matrix = server.build_comparison_baseline_matrix(self.suite, self.outputs)
        self.assertEqual(matrix["method"], "comparison_baseline_matrix_v1")
        self.assertEqual(matrix["metadata"]["arm_count"], 4)
        self.assertEqual(matrix["metadata"]["case_count"], 3)
        self.assertEqual([a["arm_id"] for a in matrix["arms"]],
                         ["human", "generic", "project", "model_x"])
        # Every case exposes the same arm ids.
        declared = {a["arm_id"] for a in matrix["arms"]}
        for case in matrix["cases"]:
            self.assertEqual({a["arm_id"] for a in case["arms"]}, declared)

    def test_identity_separated_from_evaluator_fields(self):
        matrix = server.build_comparison_baseline_matrix(self.suite, self.outputs)
        # Arm descriptors and case arms carry no identity fields.
        blob = json.dumps({"arms": matrix["arms"], "cases": matrix["cases"]}, ensure_ascii=False)
        for leak in ("example-provider", "example-model", "provider", "version"):
            self.assertNotIn(leak, blob)
        # Identity is retained in the separate identity_map.
        self.assertEqual(matrix["identity_map"]["model_x"]["model"], "example-model-x")
        self.assertEqual(matrix["identity_map"]["human"], {})

    def test_arm_types_recorded(self):
        matrix = server.build_comparison_baseline_matrix(self.suite, self.outputs)
        self.assertEqual(
            matrix["metadata"]["arm_types"],
            ["generic_prompt", "human", "model", "project_prompt"],
        )

    def test_missing_output_becomes_empty_string(self):
        outputs = {"arms": [{"arm_id": "human", "arm_type": "human",
                             "outputs": {"bench-001": "x"}}]}
        matrix = server.build_comparison_baseline_matrix(self.suite, outputs)
        case2 = next(c for c in matrix["cases"] if c["case_id"] == "bench-002")
        self.assertEqual(case2["arms"][0]["output"], "")

    def test_matrix_is_json_serializable(self):
        json.dumps(server.build_comparison_baseline_matrix(self.suite, self.outputs), ensure_ascii=False)


class ComparisonValidationTest(unittest.TestCase):
    def setUp(self):
        self.suite = server.load_benchmark_suite(SUITE)
        self.outputs = _load(OUTPUTS)
        self.matrix = server.build_comparison_baseline_matrix(self.suite, self.outputs)

    def test_valid_matrix_passes(self):
        report = server.validate_comparison_baseline_matrix(self.matrix)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["arm_ids"], ["generic", "human", "model_x", "project"])
        self.assertEqual(report["case_count"], 3)

    def test_unsupported_arm_type_fails(self):
        bad = json.loads(json.dumps(self.matrix))
        bad["arms"][0]["arm_type"] = "robot"
        report = server.validate_comparison_baseline_matrix(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("unsupported arm_type" in e for e in report["errors"]))

    def test_leaked_identity_field_fails(self):
        bad = json.loads(json.dumps(self.matrix))
        bad["arms"][0]["model"] = "leaked"
        report = server.validate_comparison_baseline_matrix(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("leaks identity field" in e for e in report["errors"]))

    def test_duplicate_arm_id_fails(self):
        bad = json.loads(json.dumps(self.matrix))
        bad["arms"][1]["arm_id"] = bad["arms"][0]["arm_id"]
        report = server.validate_comparison_baseline_matrix(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate arm_id" in e for e in report["errors"]))

    def test_mismatched_case_arms_fail(self):
        bad = json.loads(json.dumps(self.matrix))
        bad["cases"][0]["arms"].pop()
        report = server.validate_comparison_baseline_matrix(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("do not match declared arm ids" in e for e in report["errors"]))

    def test_empty_output_warns_not_fails(self):
        m = json.loads(json.dumps(self.matrix))
        m["cases"][0]["arms"][0]["output"] = ""
        report = server.validate_comparison_baseline_matrix(m)
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("empty output" in w for w in report["warnings"]))

    def test_non_object_matrix_fails_safely(self):
        report = server.validate_comparison_baseline_matrix(["nope"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["case_count"], 0)


class ComparisonSummaryTest(unittest.TestCase):
    def setUp(self):
        self.suite = server.load_benchmark_suite(SUITE)
        self.outputs = _load(OUTPUTS)
        self.matrix = server.build_comparison_baseline_matrix(self.suite, self.outputs)
        self.scores = _load(SCORES)

    def test_summary_aggregates_per_arm(self):
        summary = server.summarize_comparison_baseline_scores(self.matrix, self.scores)
        self.assertEqual(summary["method"], "comparison_baseline_summary_v1")
        self.assertTrue(summary["has_scores"])
        by_arm = {r["arm_id"]: r for r in summary["per_arm"]}
        self.assertEqual(by_arm["human"]["mean_score"], 5.0)
        self.assertEqual(by_arm["generic"]["mean_score"], 2.0)
        # human has the top mean.
        self.assertEqual(summary["best_arm_id"], "human")
        # identity joined for the model arm.
        self.assertEqual(by_arm["model_x"]["identity"]["model"], "example-model-x")

    def test_summary_without_scores_invents_nothing(self):
        summary = server.summarize_comparison_baseline_scores(self.matrix, None)
        self.assertFalse(summary["has_scores"])
        self.assertIsNone(summary["best_arm_id"])
        for row in summary["per_arm"]:
            self.assertIsNone(row["mean_score"])
            self.assertEqual(row["numeric_count"], 0)

    def test_summary_ignores_non_numeric_scores(self):
        reviews = {"scores": {"bench-001": {"human": "great", "generic": 3}}}
        summary = server.summarize_comparison_baseline_scores(self.matrix, reviews)
        by_arm = {r["arm_id"]: r for r in summary["per_arm"]}
        self.assertIsNone(by_arm["human"]["mean_score"])
        self.assertEqual(by_arm["human"]["score_count"], 1)
        self.assertEqual(by_arm["generic"]["mean_score"], 3.0)

    def test_summary_accepts_blind_reveal_cases_scored_shape(self):
        reveal_like = {"cases_scored": [
            {"case_id": "bench-001", "scores": {"human": 4, "generic": 2}},
            {"case_id": "bench-002", "scores": {"human": 5, "generic": 1}},
        ]}
        summary = server.summarize_comparison_baseline_scores(self.matrix, reveal_like)
        by_arm = {r["arm_id"]: r for r in summary["per_arm"]}
        self.assertEqual(by_arm["human"]["mean_score"], 4.5)

    def test_summary_is_json_serializable(self):
        json.dumps(server.summarize_comparison_baseline_scores(self.matrix, self.scores), ensure_ascii=False)


class ComparisonCliTest(unittest.TestCase):
    def _run_cli(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "validate_comparison_baselines", ROOT / "tools" / "validate_comparison_baselines.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_validate_exit_zero(self):
        code = self._run_cli(["--suite", str(SUITE), "--outputs", str(OUTPUTS), "--json"])
        self.assertEqual(code, 0)

    def test_cli_scores_exit_zero(self):
        code = self._run_cli(["--suite", str(SUITE), "--outputs", str(OUTPUTS),
                              "--scores", str(SCORES), "--json"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
