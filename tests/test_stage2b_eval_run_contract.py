"""Stage 2B evaluation-run contract / manifest tests.

Honesty guard: the default state has no real eval run, the placeholder example must
not validate as a real run, and nothing here reads artifact files or checks a
ROADMAP parent. A validating manifest is declared-shape only, never a real run.
"""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
CONTRACT_DOC = ROOT / "docs" / "STAGE2B_EVAL_RUN_CONTRACT.md"
EXAMPLE_FILE = ROOT / "examples" / "stage2b_eval_run.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _complete_manifest():
    metrics = {m: 0.5 for m in server.STAGE2B_EVAL_RUN_REQUIRED_METRICS}
    metrics["latency_p50"] = 100.0
    metrics["latency_p95"] = 200.0
    return {
        "run_id": "run-2026-001",
        "created_at": "2026-07-01T00:00:00",
        "dataset_id": "ds1",
        "dataset_readiness_status": "ready_real",
        "query_count": 60,
        "config": {"bm25": {"k1": 1.2, "b": 0.75}},
        "artifacts": {
            "qrels": {"path": "q.txt", "sha256": "abc123"},
            "runfile": {"path": "r.txt", "sha256": "def456"},
            "result_snapshot": {"path": "res.json", "sha256": "ghi789"},
        },
        "metrics": metrics,
        "acceptance": {"verdict": "pass", "rollback_notes": "revert to lexical baseline"},
    }


class ContractDefaultTest(unittest.TestCase):
    def test_default_not_ready_and_no_parent_check(self):
        r = server.build_stage2b_eval_run_contract()
        self.assertFalse(r["has_real_eval_run"])
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertTrue(r["example_is_placeholder_only"])

    def test_required_metrics_and_fields_exposed(self):
        r = server.build_stage2b_eval_run_contract()
        for m in ("ndcg@k", "mrr", "map", "recall@k", "precision@k",
                  "latency_p50", "latency_p95", "miss_rate", "refusal_insufficiency_rate"):
            self.assertIn(m, r["required_metrics"])
        for f in ("run_id", "dataset_readiness_status", "query_count", "artifacts", "acceptance"):
            self.assertIn(f, r["required_fields"])

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_eval_run_contract(), ensure_ascii=False)


class ExampleFileTest(unittest.TestCase):
    def test_example_exists_and_parses(self):
        self.assertTrue(EXAMPLE_FILE.exists())
        json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))

    def test_example_is_not_a_ready_real_run(self):
        data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        v = server.validate_stage2b_eval_run_manifest(data)
        self.assertFalse(v["ready"])
        self.assertTrue(v["errors"])


class ManifestValidationTest(unittest.TestCase):
    def test_complete_manifest_validates_without_reading_files(self):
        v = server.validate_stage2b_eval_run_manifest(_complete_manifest())
        self.assertTrue(v["passed"], v["errors"])
        self.assertTrue(v["ready"])

    def test_template_marker_fails(self):
        m = _complete_manifest()
        m["is_template"] = True
        self.assertFalse(server.validate_stage2b_eval_run_manifest(m)["ready"])

    def test_non_ready_real_status_fails(self):
        m = _complete_manifest()
        m["dataset_readiness_status"] = "incomplete_real"
        self.assertFalse(server.validate_stage2b_eval_run_manifest(m)["ready"])

    def test_missing_metric_fails(self):
        m = _complete_manifest()
        del m["metrics"]["ndcg@k"]
        v = server.validate_stage2b_eval_run_manifest(m)
        self.assertFalse(v["ready"])
        self.assertTrue(any("ndcg@k" in e for e in v["errors"]))

    def test_non_numeric_metric_fails(self):
        m = _complete_manifest()
        m["metrics"]["mrr"] = "high"
        self.assertFalse(server.validate_stage2b_eval_run_manifest(m)["ready"])

    def test_bad_latency_ordering_fails(self):
        m = _complete_manifest()
        m["metrics"]["latency_p95"] = 50.0  # < p50
        v = server.validate_stage2b_eval_run_manifest(m)
        self.assertFalse(v["ready"])
        self.assertTrue(any("latency_p95" in e for e in v["errors"]))

    def test_query_count_out_of_band_fails(self):
        for qc in (10, 200):
            m = _complete_manifest()
            m["query_count"] = qc
            self.assertFalse(server.validate_stage2b_eval_run_manifest(m)["ready"])

    def test_placeholder_artifact_hash_fails(self):
        m = _complete_manifest()
        m["artifacts"]["qrels"]["sha256"] = "PLACEHOLDER"
        self.assertFalse(server.validate_stage2b_eval_run_manifest(m)["ready"])

    def test_missing_acceptance_verdict_fails(self):
        m = _complete_manifest()
        m["acceptance"] = {}
        self.assertFalse(server.validate_stage2b_eval_run_manifest(m)["ready"])

    def test_non_object_fails_safely(self):
        self.assertFalse(server.validate_stage2b_eval_run_manifest(["nope"])["passed"])


class ContractDocTest(unittest.TestCase):
    def test_doc_exists_and_states_shape_only(self):
        self.assertTrue(CONTRACT_DOC.exists())
        text = CONTRACT_DOC.read_text(encoding="utf-8").lower()
        self.assertIn("shape", text)
        self.assertIn("does not prove", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_eval_run_contract", ROOT / "tools" / "check_stage2b_eval_run_contract.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_example_exits_one(self):
        self.assertEqual(self._run(["--config", str(EXAMPLE_FILE), "--json"]), 1)

    def test_cli_complete_temp_manifest_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_manifest(), f, ensure_ascii=False)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
