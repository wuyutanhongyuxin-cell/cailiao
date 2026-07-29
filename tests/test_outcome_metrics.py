"""Stage 5 outcome-metrics logging + summary skeleton tests."""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
LOG = ROOT / "tests" / "data" / "outcome_metrics_sample.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _load():
    return server.load_outcome_metrics_log(LOG)


class OutcomeMetricsValidationTest(unittest.TestCase):
    def test_sample_log_validates(self):
        report = server.validate_outcome_metrics_log(_load())
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["row_count"], 4)
        self.assertEqual(report["arm_ids"], ["human", "model_x"])

    def test_missing_identity_fails(self):
        log = _load()
        log["rows"][0].pop("arm_id")
        report = server.validate_outcome_metrics_log(log)
        self.assertFalse(report["passed"])
        self.assertTrue(any("'arm_id' must be a non-empty string" in e for e in report["errors"]))

    def test_duplicate_identity_fails(self):
        log = _load()
        log["rows"][1]["case_id"] = log["rows"][0]["case_id"]
        log["rows"][1]["arm_id"] = log["rows"][0]["arm_id"]
        report = server.validate_outcome_metrics_log(log)
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate case_id/arm_id" in e for e in report["errors"]))

    def test_accepted_sections_out_of_range_fails(self):
        log = _load()
        log["rows"][0]["accepted_sections"] = 99
        report = server.validate_outcome_metrics_log(log)
        self.assertFalse(report["passed"])
        self.assertTrue(any("within [0, total_sections]" in e for e in report["errors"]))

    def test_bad_timestamp_without_duration_fails(self):
        log = _load()
        row = log["rows"][0]
        row["started_at"] = "not-a-date"
        report = server.validate_outcome_metrics_log(log)
        self.assertFalse(report["passed"])
        self.assertTrue(any("ISO-like timestamps" in e for e in report["errors"]))

    def test_completed_before_started_fails(self):
        log = _load()
        row = log["rows"][0]
        row["completed_at"] = "2026-07-01T08:00:00Z"  # before started_at
        report = server.validate_outcome_metrics_log(log)
        self.assertFalse(report["passed"])
        self.assertTrue(any("must not precede" in e for e in report["errors"]))

    def test_missing_optional_metric_warns_not_fails(self):
        log = {"metadata": {"name": "x"},
               "rows": [{"case_id": "c1", "arm_id": "a1", "accepted": True}]}
        report = server.validate_outcome_metrics_log(log)
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("edit distance" in w for w in report["warnings"]))

    def test_non_object_log_fails_safely(self):
        report = server.validate_outcome_metrics_log(["nope"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["row_count"], 0)

    def test_load_missing_rows_raises(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"metadata": {}}, f)
            path = f.name
        with self.assertRaises(ValueError):
            server.load_outcome_metrics_log(path)


class OutcomeMetricsSummaryTest(unittest.TestCase):
    def test_per_row_metrics_are_deterministic_values(self):
        summary = server.summarize_outcome_metrics(_load())
        self.assertEqual(summary["method"], "outcome_metrics_summary_v1")
        by_id = {(r["case_id"], r["arm_id"]): r["metrics"] for r in summary["per_row"]}
        # bench-001/human: identical texts -> edit_distance 0; 12 minutes -> 720s; 4/4 adoption.
        m = by_id[("bench-001", "human")]
        self.assertEqual(m["adoption_rate"], 1.0)
        self.assertEqual(m["edit_distance"], 0)
        self.assertEqual(m["duration_seconds"], 720.0)
        self.assertEqual(m["rework_rounds"], 0)
        # bench-001/model_x: accepted False -> 0.0; computed edit distance 6; supplied duration.
        m2 = by_id[("bench-001", "model_x")]
        self.assertEqual(m2["adoption_rate"], 0.0)
        self.assertEqual(m2["edit_distance"], 6)
        self.assertEqual(m2["duration_seconds"], 1800.0)
        # bench-002/human: 3/4 adoption; supplied edit_distance 12; naive-timestamp duration 1200s.
        m3 = by_id[("bench-002", "human")]
        self.assertEqual(m3["adoption_rate"], 0.75)
        self.assertEqual(m3["edit_distance"], 12)
        self.assertEqual(m3["duration_seconds"], 1200.0)
        # bench-002/model_x: no texts and no edit_distance -> None.
        self.assertIsNone(by_id[("bench-002", "model_x")]["edit_distance"])

    def test_per_arm_and_overall_aggregates(self):
        summary = server.summarize_outcome_metrics(_load())
        by_arm = {r["arm_id"]: r for r in summary["per_arm"]}
        self.assertEqual(by_arm["human"]["metrics"]["adoption_rate"], 0.875)
        self.assertEqual(by_arm["human"]["metrics"]["duration_seconds"], 960.0)
        self.assertEqual(by_arm["model_x"]["metrics"]["adoption_rate"], 0.5)
        # model_x edit_distance mean uses only the one present value (6).
        self.assertEqual(by_arm["model_x"]["metrics"]["edit_distance"], 6.0)
        self.assertEqual(summary["overall"]["adoption_rate"], 0.6875)
        self.assertEqual(summary["overall"]["rework_rounds"], 1.0)

    def test_levenshtein_basic(self):
        self.assertEqual(server._levenshtein("kitten", "sitting"), 3)
        self.assertEqual(server._levenshtein("", "abc"), 3)
        self.assertEqual(server._levenshtein("abc", "abc"), 0)

    def test_summary_is_json_serializable_and_deterministic(self):
        a = server.summarize_outcome_metrics(copy.deepcopy(_load()))
        b = server.summarize_outcome_metrics(copy.deepcopy(_load()))
        self.assertEqual(a, b)
        json.dumps(a, ensure_ascii=False)


class OutcomeMetricsCliTest(unittest.TestCase):
    def _run_cli(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "validate_outcome_metrics", ROOT / "tools" / "validate_outcome_metrics.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_validate_exit_zero(self):
        self.assertEqual(self._run_cli(["--log", str(LOG), "--json"]), 0)

    def test_cli_summary_exit_zero(self):
        self.assertEqual(self._run_cli(["--log", str(LOG), "--json", "--summary"]), 0)


if __name__ == "__main__":
    unittest.main()
