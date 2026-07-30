"""Stage 2 real reranker/cross-encoder + RRF production-readiness scaffold tests.

Honesty guard: no real cross-encoder exists in this repo. Tests build DECLARED
configs and rank lists in memory to exercise the readiness gate and the pure-rank
RRF helper; a "ready" verdict means the config is complete, not that a model ran.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _complete_config():
    return {
        "provider": {"provider": "cohere", "model": "rerank-3.5",
                     "credential_source": "COHERE_API_KEY", "eval_metrics": ["ndcg", "mrr"]},
        "rrf": {"rank_constant": 60, "rank_window_size": 100, "channels": ["bm25", "vector", "rerank"]},
    }


class RerankerProviderReadinessTest(unittest.TestCase):
    def test_default_is_local_not_real(self):
        r = server.build_reranker_provider_readiness()
        self.assertTrue(r["is_local_test_reranker"])
        self.assertFalse(r["is_real_provider_declared"])

    def test_local_mode_not_real(self):
        r = server.build_reranker_provider_readiness({"mode": "deterministic_local"})
        self.assertFalse(r["is_real_provider_declared"])

    def test_real_provider_needs_model_credential_metrics(self):
        r = server.build_reranker_provider_readiness({"provider": "cohere"})
        self.assertFalse(r["is_real_provider_declared"])
        self.assertTrue(r["missing"])

    def test_complete_real_provider_declared(self):
        r = server.build_reranker_provider_readiness(_complete_config()["provider"])
        self.assertTrue(r["is_real_provider_declared"])
        self.assertEqual(r["missing"], [])
        self.assertIn("ndcg", r["eval_metrics"])

    def test_credential_value_rejected_source_accepted(self):
        bad = server.validate_reranker_provider_config(
            {"provider": "cohere", "model": "m", "eval_metrics": ["mrr"], "api_key": "sk"})
        self.assertFalse(bad["passed"])
        ok = server.validate_reranker_provider_config(_complete_config()["provider"])
        self.assertTrue(ok["passed"], ok["errors"])


class RrfFusionTest(unittest.TestCase):
    def test_rrf_is_deterministic_and_rank_based(self):
        rs = [["a", "b", "c"], ["b", "c", "a"], ["c", "a"]]
        a = server.fuse_ranked_results_rrf(rs)
        b = server.fuse_ranked_results_rrf(rs)
        self.assertEqual(a, b)
        # a and c tie on score; stable tie-break by id ascending puts a before c.
        self.assertEqual([e["id"] for e in a][:2], ["a", "c"])
        self.assertEqual(a[0]["rank"], 1)

    def test_rrf_ignores_score_scale(self):
        # Passing dict entries with wildly different implied scores must not matter;
        # only rank position is used.
        rs = [[{"id": "x", "rank": 1}, {"id": "y", "rank": 2}]]
        fused = server.fuse_ranked_results_rrf(rs)
        self.assertEqual([e["id"] for e in fused], ["x", "y"])

    def test_rank_window_truncates(self):
        rs = [["a", "b", "c"], ["b", "c", "a"], ["c", "a"]]
        fused = server.fuse_ranked_results_rrf(rs, rank_window_size=1)
        # Only rank-1 of each set contributes: a, b, c each once -> id-sorted.
        self.assertEqual([e["id"] for e in fused], ["a", "b", "c"])

    def test_plan_defaults_and_validation(self):
        plan = server.build_rrf_fusion_plan()
        self.assertEqual(plan["rank_constant"], server.RRF_K)
        self.assertTrue(server.validate_rrf_fusion_plan(plan)["passed"])

    def test_invalid_rank_constant_and_window_rejected(self):
        self.assertFalse(server.validate_rrf_fusion_plan(
            {"rank_constant": 0, "rank_window_size": 10, "channels": ["bm25"]})["passed"])
        self.assertFalse(server.validate_rrf_fusion_plan(
            {"rank_constant": 60, "rank_window_size": 0, "channels": ["bm25"]})["passed"])
        self.assertFalse(server.validate_rrf_fusion_plan(
            {"rank_constant": 60, "rank_window_size": 10, "channels": []})["passed"])


class RerankPipelineReadinessTest(unittest.TestCase):
    def test_default_repo_state_not_production_ready(self):
        r = server.build_rerank_pipeline_readiness()
        self.assertFalse(r["production_ready"])
        self.assertTrue(r["missing"])
        self.assertFalse(r["current_shipped_state"]["production_ready"])
        self.assertFalse(r["current_shipped_state"]["is_real_rerank_model"])

    def test_complete_declared_config_ready_without_network(self):
        r = server.build_rerank_pipeline_readiness(_complete_config())
        self.assertTrue(r["production_ready"], r["missing"])
        self.assertEqual(r["missing"], [])

    def test_real_provider_but_invalid_rrf_not_ready(self):
        cfg = _complete_config()
        cfg["rrf"] = {"rank_constant": 0}  # invalid
        r = server.build_rerank_pipeline_readiness(cfg)
        self.assertFalse(r["production_ready"])
        self.assertTrue(any("RRF" in m for m in r["missing"]))

    def test_missing_eval_metrics_not_ready(self):
        cfg = _complete_config()
        cfg["provider"] = {"provider": "cohere", "model": "rerank-3.5", "credential_source": "COHERE_API_KEY"}
        r = server.build_rerank_pipeline_readiness(cfg)
        self.assertFalse(r["production_ready"])

    def test_readiness_is_json_serializable(self):
        json.dumps(server.build_rerank_pipeline_readiness(_complete_config()), ensure_ascii=False)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_rerank_production_readiness", ROOT / "tools" / "check_rerank_production_readiness.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_nonzero(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_complete_config_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_config(), f)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
