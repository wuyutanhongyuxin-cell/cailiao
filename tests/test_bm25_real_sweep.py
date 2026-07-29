"""Stage 5 gated BM25 sweep calibration scaffold tests.

Honesty guard: no real query set is present. Tests build minimal synthetic
ready-shaped cases + a tiny corpus in memory to exercise the gated sweep; the
gate itself is what these tests mainly verify.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
TEMPLATE = ROOT / "tests" / "data" / "real_query_set_template.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _case(i):
    return {
        "id": f"q{i:03d}",
        "query": f"专项工作通知 关键词{i}",
        "provenance": {"source": "intake", "collected_at": "2026-07-01T00:00:00", "anonymized": True},
        "relevant_titles": [f"文件{i}"],
    }


def _doc(i):
    return {"title": f"文件{i}", "text": f"专项工作通知 关键词{i} 正文内容，各科室落实。",
            "format": "txt", "status": "有效"}


def _ready_dataset_with_corpus(n=50):
    return {
        "metadata": {"name": "real intake set", "version": "v1"},
        "cases": [_case(i) for i in range(n)],
        "corpus": [_doc(i) for i in range(n)],
    }


class SweepGridTest(unittest.TestCase):
    def test_default_grid_is_deterministic_and_valid(self):
        a = server.build_bm25_sweep_grid()
        b = server.build_bm25_sweep_grid()
        self.assertEqual(a, b)
        self.assertEqual(a["combination_count"], len(a["k1"]) * len(a["b"]) * len(a["thresholds"]))
        self.assertTrue(server.validate_bm25_sweep_config(a)["passed"])

    def test_overrides_are_cleaned_and_sorted(self):
        grid = server.build_bm25_sweep_grid({"k1": [1.5, 0.9, 1.5], "b": [2.0, 0.5], "thresholds": [0.2]})
        self.assertEqual(grid["k1"], [0.9, 1.5])       # dedup + sort
        self.assertEqual(grid["b"], [0.5])             # 2.0 out of range dropped
        self.assertEqual(grid["thresholds"], [0.2])

    def test_invalid_grid_fails_validation(self):
        self.assertFalse(server.validate_bm25_sweep_config({"k1": [], "b": [0.5], "thresholds": [0.0]})["passed"])
        self.assertFalse(server.validate_bm25_sweep_config({"k1": [1.2], "b": [1.5], "thresholds": [0.0]})["passed"])
        self.assertFalse(server.validate_bm25_sweep_config("nope")["passed"])


class SweepGateTest(unittest.TestCase):
    def test_template_is_refused(self):
        dataset = server.load_real_query_set(TEMPLATE)
        report = server.run_bm25_sweep_on_real_query_set(dataset)
        self.assertFalse(report["ran"])
        self.assertTrue(report["refused"])
        self.assertIn("not ready_real", report["reason"])

    def test_incomplete_set_is_refused(self):
        dataset = {"metadata": {"name": "real"}, "cases": [_case(i) for i in range(10)],
                   "corpus": [_doc(i) for i in range(10)]}
        report = server.run_bm25_sweep_on_real_query_set(dataset)
        self.assertFalse(report["ran"])
        self.assertTrue(report["refused"])

    def test_ready_real_without_corpus_is_refused(self):
        dataset = {"metadata": {"name": "real"}, "cases": [_case(i) for i in range(50)]}
        report = server.run_bm25_sweep_on_real_query_set(dataset)
        self.assertFalse(report["ran"])
        self.assertIn("no 'corpus'", report["reason"])

    def test_synthetic_marker_is_refused(self):
        dataset = _ready_dataset_with_corpus(50)
        dataset["metadata"]["name"] = "synthetic set"
        report = server.run_bm25_sweep_on_real_query_set(dataset)
        self.assertFalse(report["ran"])


class SweepRunTest(unittest.TestCase):
    def test_ready_real_with_corpus_runs_deterministic_sweep(self):
        dataset = _ready_dataset_with_corpus(50)
        report = server.run_bm25_sweep_on_real_query_set(dataset)
        self.assertTrue(report["ran"])
        self.assertFalse(report["refused"])
        # 4 k1 x 3 b x 3 thresholds = 36 candidates by default.
        self.assertEqual(report["candidate_count"], 36)
        for field in ("k1", "b", "threshold", "title_recall_at_k", "chunk_recall_at_k", "miss_count"):
            self.assertIn(field, report["best"])
        self.assertIn("boundary", report)

    def test_sweep_decision_is_deterministic(self):
        # The calibration DECISION (chosen k1/b/threshold + recall/miss) is stable.
        # A single-combo grid keeps this fast and isolates the decision fields.
        # (title_mrr can jitter on score ties in the retrieval layer and is not a
        # decision field, so it is intentionally excluded from this assertion.)
        dataset = _ready_dataset_with_corpus(50)
        cfg = {"k1": [1.2], "b": [0.75], "thresholds": [0.0]}
        a = server.run_bm25_sweep_on_real_query_set(dataset, cfg)
        b = server.run_bm25_sweep_on_real_query_set(dataset, cfg)
        decision = lambda r: {k: r["best"][k] for k in
                              ("k1", "b", "threshold", "title_recall_at_k", "chunk_recall_at_k", "miss_count")}
        self.assertEqual(decision(a), decision(b))
        self.assertEqual(a["candidate_count"], b["candidate_count"])

    def test_report_is_json_serializable(self):
        json.dumps(server.run_bm25_sweep_on_real_query_set(_ready_dataset_with_corpus(50)), ensure_ascii=False)


class SweepCliTest(unittest.TestCase):
    def _run_cli(self, path):
        spec_cli = importlib.util.spec_from_file_location(
            "sweep_bm25_real_queries", ROOT / "tools" / "sweep_bm25_real_queries.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(["--set", str(path), "--json"])

    def test_cli_refuses_template_nonzero(self):
        self.assertEqual(self._run_cli(TEMPLATE), 1)

    def test_cli_runs_on_ready_real_written_to_tmp(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_ready_dataset_with_corpus(50), f, ensure_ascii=False)
            path = f.name
        self.assertEqual(self._run_cli(path), 0)


if __name__ == "__main__":
    unittest.main()
