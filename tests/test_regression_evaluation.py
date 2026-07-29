"""Stage 5 regression-evaluation runner skeleton tests."""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
CONFIG = ROOT / "tests" / "data" / "regression_run_sample.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _config():
    with CONFIG.open("r", encoding="utf-8") as f:
        return json.load(f)


class RegressionBuildTest(unittest.TestCase):
    def test_build_normalizes_metadata_and_reports(self):
        run = server.build_regression_evaluation_run(_config())
        self.assertEqual(run["method"], "regression_evaluation_run_v1")
        md = run["metadata"]
        self.assertEqual(md["trigger_kind"], "rules_update")
        self.assertEqual(md["baseline_ref"], "rules-v1")
        self.assertEqual(md["candidate_ref"], "rules-v2")
        self.assertEqual(md["report_count"], 3)
        # Metric pairs are normalized to float baseline/candidate.
        bs = next(r for r in run["reports"] if r["name"] == "benchmark_scoring")
        self.assertEqual(bs["metrics"]["overall"], {"baseline": 0.82, "candidate": 0.9})

    def test_bare_numeric_metric_becomes_candidate_only(self):
        cfg = {"trigger_kind": "manual", "reports": [
            {"name": "r1", "kind": "retrieval_eval", "metrics": {"recall": 0.7}}]}
        run = server.build_regression_evaluation_run(cfg)
        self.assertEqual(run["reports"][0]["metrics"]["recall"], {"candidate": 0.7})

    def test_build_is_json_serializable(self):
        json.dumps(server.build_regression_evaluation_run(_config()), ensure_ascii=False)


class RegressionValidationTest(unittest.TestCase):
    def setUp(self):
        self.run = server.build_regression_evaluation_run(_config())

    def test_valid_run_passes(self):
        report = server.validate_regression_evaluation_run(self.run)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["report_count"], 3)
        self.assertEqual(report["report_names"],
                         ["benchmark_scoring", "comparison_matrix", "outcome_metrics"])

    def test_unsupported_trigger_fails(self):
        run = copy.deepcopy(self.run)
        run["metadata"]["trigger_kind"] = "cron"
        report = server.validate_regression_evaluation_run(run)
        self.assertFalse(report["passed"])
        self.assertTrue(any("trigger_kind' unsupported" in e for e in report["errors"]))

    def test_missing_metadata_field_fails(self):
        run = copy.deepcopy(self.run)
        run["metadata"]["baseline_ref"] = ""
        report = server.validate_regression_evaluation_run(run)
        self.assertFalse(report["passed"])
        self.assertTrue(any("baseline_ref" in e for e in report["errors"]))

    def test_duplicate_report_name_fails(self):
        run = copy.deepcopy(self.run)
        run["reports"][1]["name"] = run["reports"][0]["name"]
        report = server.validate_regression_evaluation_run(run)
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate report name" in e for e in report["errors"]))

    def test_non_numeric_metric_delta_fails(self):
        run = copy.deepcopy(self.run)
        run["reports"][0]["metrics"]["overall"]["candidate"] = "high"
        report = server.validate_regression_evaluation_run(run)
        self.assertFalse(report["passed"])
        self.assertTrue(any("must be numeric" in e for e in report["errors"]))

    def test_unknown_report_kind_warns(self):
        run = copy.deepcopy(self.run)
        run["reports"][0]["kind"] = "mystery"
        report = server.validate_regression_evaluation_run(run)
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("unknown report kind" in w for w in report["warnings"]))

    def test_no_reports_warns_not_fails(self):
        run = server.build_regression_evaluation_run(
            {"trigger_kind": "manual", "baseline_ref": "a", "candidate_ref": "b", "suite": "s"})
        report = server.validate_regression_evaluation_run(run)
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("no component reports" in w for w in report["warnings"]))

    def test_non_object_run_fails_safely(self):
        report = server.validate_regression_evaluation_run(["nope"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["report_count"], 0)


class RegressionSummaryTest(unittest.TestCase):
    def setUp(self):
        self.run = server.build_regression_evaluation_run(_config())

    def test_summary_totals_and_deltas(self):
        summary = server.summarize_regression_evaluation_run(self.run)
        self.assertEqual(summary["method"], "regression_evaluation_summary_v1")
        self.assertEqual(summary["totals"], {"passed": 11, "failed": 0, "warnings": 1})
        by_name = {r["name"]: r for r in summary["reports"]}
        self.assertEqual(by_name["benchmark_scoring"]["metric_deltas"]["overall"], 0.08)
        self.assertEqual(by_name["outcome_metrics"]["metric_deltas"]["adoption_rate"], 0.0875)

    def test_needs_review_when_deltas_present(self):
        # The sample passes every report but has metric deltas + a warning.
        summary = server.summarize_regression_evaluation_run(self.run)
        self.assertEqual(summary["status"], "needs_review")

    def test_failed_when_a_report_fails(self):
        run = copy.deepcopy(self.run)
        run["reports"][0]["status"] = "failed"
        run["reports"][0]["counts"]["failed"] = 1
        summary = server.summarize_regression_evaluation_run(run)
        self.assertEqual(summary["status"], "failed")

    def test_passed_when_clean_and_no_deltas(self):
        run = server.build_regression_evaluation_run({
            "trigger_kind": "manual", "baseline_ref": "a", "candidate_ref": "b", "suite": "s",
            "reports": [{"name": "r1", "kind": "retrieval_eval", "status": "passed",
                         "counts": {"passed": 2, "failed": 0, "warnings": 0}}],
        })
        summary = server.summarize_regression_evaluation_run(run)
        self.assertEqual(summary["status"], "passed")

    def test_summary_is_json_serializable_and_deterministic(self):
        a = server.summarize_regression_evaluation_run(server.build_regression_evaluation_run(_config()))
        b = server.summarize_regression_evaluation_run(server.build_regression_evaluation_run(_config()))
        self.assertEqual(a, b)
        json.dumps(a, ensure_ascii=False)


class RegressionCliTest(unittest.TestCase):
    def test_cli_exit_zero(self):
        spec_cli = importlib.util.spec_from_file_location(
            "run_regression_evaluation", ROOT / "tools" / "run_regression_evaluation.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--config", str(CONFIG), "--json"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
