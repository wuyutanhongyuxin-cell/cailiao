"""Stage 5 blind-evaluation packaging + reveal skeleton tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
SUITE = ROOT / "tests" / "data" / "benchmark_suite_sample.json"
CANDIDATES = ROOT / "tests" / "data" / "blind_eval_candidates_sample.json"
SCORES = ROOT / "tests" / "data" / "blind_eval_scores_sample.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _load(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


class BlindEvaluationPackTest(unittest.TestCase):
    def setUp(self):
        self.suite = server.load_benchmark_suite(SUITE)
        self.candidates = _load(CANDIDATES)

    def test_pack_hides_identity_and_keeps_answers(self):
        pack = server.build_blind_evaluation_pack(self.suite, self.candidates)
        self.assertEqual(pack["method"], "blind_evaluation_pack_v1")
        self.assertEqual(pack["evaluator_view"]["blind_ids"], ["candidate_a", "candidate_b"])
        # No identity field anywhere in the evaluator view.
        blob = json.dumps(pack["evaluator_view"], ensure_ascii=False)
        for leak in ("example-provider", "example-model", "provider", "model", "version"):
            self.assertNotIn(leak, blob)
        # Answers are preserved and attached to case + blind id.
        first_case = pack["evaluator_view"]["cases"][0]
        self.assertEqual(first_case["case_id"], "bench-001")
        answers = {c["blind_id"]: c["answer"] for c in first_case["candidates"]}
        self.assertIn("特此通知", answers["candidate_a"])
        # Reveal map retains identity in a separate, non-evaluator section.
        self.assertEqual(pack["reveal_map"]["candidate_a"]["model"], "example-model-x")

    def test_every_case_has_same_blind_ids(self):
        pack = server.build_blind_evaluation_pack(self.suite, self.candidates)
        declared = set(pack["evaluator_view"]["blind_ids"])
        for case in pack["evaluator_view"]["cases"]:
            self.assertEqual({c["blind_id"] for c in case["candidates"]}, declared)

    def test_missing_answer_becomes_empty_string(self):
        candidates = {"candidates": [{"provider": "p", "model": "m", "answers": {"bench-001": "x"}}]}
        pack = server.build_blind_evaluation_pack(self.suite, candidates)
        # bench-002 has no answer for this candidate -> empty string, still present.
        case2 = next(c for c in pack["evaluator_view"]["cases"] if c["case_id"] == "bench-002")
        self.assertEqual(case2["candidates"][0]["answer"], "")

    def test_pack_is_json_serializable(self):
        json.dumps(server.build_blind_evaluation_pack(self.suite, self.candidates), ensure_ascii=False)


class BlindEvaluationValidationTest(unittest.TestCase):
    def setUp(self):
        self.suite = server.load_benchmark_suite(SUITE)
        self.candidates = _load(CANDIDATES)
        self.pack = server.build_blind_evaluation_pack(self.suite, self.candidates)

    def test_valid_pack_passes(self):
        report = server.validate_blind_evaluation_pack(self.pack)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["blind_ids"], ["candidate_a", "candidate_b"])
        self.assertEqual(report["case_count"], 3)

    def test_leaked_identity_field_fails(self):
        bad = json.loads(json.dumps(self.pack))
        bad["evaluator_view"]["cases"][0]["candidates"][0]["model"] = "leaked-model"
        report = server.validate_blind_evaluation_pack(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("leaks identity field" in e for e in report["errors"]))

    def test_mismatched_blind_ids_fail(self):
        bad = json.loads(json.dumps(self.pack))
        bad["evaluator_view"]["cases"][0]["candidates"].pop()
        report = server.validate_blind_evaluation_pack(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("do not match declared blind ids" in e for e in report["errors"]))

    def test_duplicate_case_id_fails(self):
        bad = json.loads(json.dumps(self.pack))
        bad["evaluator_view"]["cases"][1]["case_id"] = bad["evaluator_view"]["cases"][0]["case_id"]
        report = server.validate_blind_evaluation_pack(bad)
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate case_id" in e for e in report["errors"]))

    def test_non_object_pack_fails_safely(self):
        report = server.validate_blind_evaluation_pack(["nope"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["case_count"], 0)


class BlindEvaluationRevealTest(unittest.TestCase):
    def setUp(self):
        self.suite = server.load_benchmark_suite(SUITE)
        self.candidates = _load(CANDIDATES)
        self.pack = server.build_blind_evaluation_pack(self.suite, self.candidates)
        self.scores = _load(SCORES)

    def test_reveal_joins_identity_and_aggregates(self):
        result = server.reveal_blind_evaluation_results(self.pack, self.scores)
        self.assertEqual(result["method"], "blind_evaluation_reveal_v1")
        by_label = {r["blind_id"]: r for r in result["per_candidate"]}
        # candidate_a scores 5,4,5 -> mean 4.6667; identity revealed.
        self.assertAlmostEqual(by_label["candidate_a"]["mean_score"], round((5 + 4 + 5) / 3, 4))
        self.assertEqual(by_label["candidate_a"]["identity"]["model"], "example-model-x")
        self.assertEqual(by_label["candidate_b"]["mean_score"], 2.6667)

    def test_non_numeric_reviews_counted_not_averaged(self):
        reviews = {"scores": {"bench-001": {"candidate_a": "good", "candidate_b": 3}}}
        result = server.reveal_blind_evaluation_results(self.pack, reviews)
        by_label = {r["blind_id"]: r for r in result["per_candidate"]}
        self.assertEqual(by_label["candidate_a"]["score_count"], 1)
        self.assertEqual(by_label["candidate_a"]["numeric_count"], 0)
        self.assertIsNone(by_label["candidate_a"]["mean_score"])
        self.assertEqual(by_label["candidate_b"]["mean_score"], 3.0)

    def test_reveal_is_json_serializable(self):
        json.dumps(server.reveal_blind_evaluation_results(self.pack, self.scores), ensure_ascii=False)


class BlindEvaluationCliTest(unittest.TestCase):
    def _run_cli(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "validate_blind_eval", ROOT / "tools" / "validate_blind_eval.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_validate_exit_zero(self):
        code = self._run_cli(["--suite", str(SUITE), "--candidates", str(CANDIDATES), "--json"])
        self.assertEqual(code, 0)

    def test_cli_reveal_exit_zero(self):
        code = self._run_cli(["--suite", str(SUITE), "--candidates", str(CANDIDATES),
                              "--reveal", str(SCORES), "--json"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
