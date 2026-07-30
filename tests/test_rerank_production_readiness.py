"""Stage 2 real reranker/cross-encoder + RRF production-readiness scaffold tests.

Honesty guard: no real cross-encoder exists in this repo. Tests build DECLARED
configs and rank lists in memory to exercise the readiness gate and the pure-rank
RRF helper; a "ready" verdict means the config is complete, not that a model ran.
"""

import contextlib
import http.server
import importlib.util
import io
import json
import hashlib
import os
import threading
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

    def test_http_compatible_provider_requires_endpoint(self):
        r = server.build_reranker_provider_readiness({
            "provider": "http_rerank_compatible",
            "model": "rerank-test",
            "credential_source": "RERANK_API_KEY",
            "eval_metrics": ["mrr"],
        })
        self.assertFalse(r["is_real_provider_declared"])
        self.assertTrue(any("endpoint_url" in m for m in r["missing"]))

    def test_http_compatible_provider_declared_with_endpoint(self):
        r = server.build_reranker_provider_readiness({
            "provider": "http_rerank_compatible",
            "endpoint_url": "https://example.test/rerank",
            "model": "rerank-test",
            "credential_source": "RERANK_API_KEY",
            "eval_metrics": ["mrr"],
        })
        self.assertTrue(r["is_real_provider_declared"], r["missing"])
        self.assertEqual(r["endpoint_host"], "example.test")


class HTTPRerankProviderTest(unittest.TestCase):
    def _serve_once(self):
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                seen["path"] = self.path
                seen["authorization"] = self.headers.get("Authorization")
                seen["body"] = json.loads(body)
                payload = json.dumps({"results": [
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.9},
                ]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt, *args):
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, seen

    def test_local_stub_request_shape_reorders_only_candidates_and_no_key_leak(self):
        httpd, seen = self._serve_once()
        old = os.environ.get("TEST_RERANK_KEY")
        os.environ["TEST_RERANK_KEY"] = "secret-rerank-key"
        try:
            provider = server.HTTPRerankProvider(
                endpoint_url=f"http://127.0.0.1:{httpd.server_address[1]}/v1/rerank",
                model="test-reranker",
                credential_source="TEST_RERANK_KEY",
                timeout_sec=5,
            )
            items = [
                {"chunk_id": "a", "content": "alpha", "hit_reasons": [], "channels": {}},
                {"chunk_id": "b", "content": "beta", "hit_reasons": [], "channels": {}},
            ]
            out = provider.rerank_items("alpha", items)
            self.assertEqual([i["chunk_id"] for i in out], ["b", "a"])
            self.assertEqual({i["chunk_id"] for i in out}, {"a", "b"})
            self.assertEqual(seen["path"], "/v1/rerank")
            self.assertEqual(seen["authorization"], "Bearer secret-rerank-key")
            self.assertEqual(seen["body"]["model"], "test-reranker")
            self.assertEqual(seen["body"]["query"], "alpha")
            self.assertIn("alpha", seen["body"]["documents"][0])
            self.assertIn("beta", seen["body"]["documents"][1])
            meta = provider.metadata()
            self.assertTrue(meta["connectivity_proven"])
            self.assertTrue(meta["is_real_rerank_model"])
            self.assertNotIn("secret-rerank-key", json.dumps(meta))
        finally:
            httpd.shutdown()
            httpd.server_close()
            if old is None:
                os.environ.pop("TEST_RERANK_KEY", None)
            else:
                os.environ["TEST_RERANK_KEY"] = old

    def test_missing_env_var_fails_without_secret_value(self):
        os.environ.pop("MISSING_RERANK_KEY", None)
        provider = server.HTTPRerankProvider(
            endpoint_url="http://127.0.0.1:9/v1/rerank",
            model="test-reranker",
            credential_source="MISSING_RERANK_KEY",
            timeout_sec=1,
        )
        with self.assertRaises(RuntimeError) as ctx:
            provider.rerank_items("q", [{"chunk_id": "a", "content": "a"}])
        self.assertIn("MISSING_RERANK_KEY", str(ctx.exception))
        self.assertNotIn("Bearer", str(ctx.exception))

    def test_placeholder_credential_source_rejected(self):
        with self.assertRaises(ValueError):
            server.HTTPRerankProvider(
                endpoint_url="http://127.0.0.1:9/v1/rerank",
                model="test-reranker",
                credential_source="sk-secret-value",
            )


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


class StubRrfRerankEvalTest(unittest.TestCase):
    def test_report_runs_and_marks_stub_not_real_provider_evidence(self):
        cases = []
        case_hashes = []
        for i in range(50):
            query = "alpha" if i % 2 == 0 else "beta"
            qhash = "sha256:" + hashlib.sha256(f"{query}-{i}".encode("utf-8")).hexdigest()
            case_hashes.append(qhash)
            cases.append({
                "id": f"q{i:02d}",
                "query": query,
                "query_sha256": qhash,
                "provenance": {"source": "test", "collected_at": "2026-01-01", "anonymized": True},
                "relevant_titles": ["doc-a" if query == "alpha" else "doc-b"],
            })
        dataset = {
            "metadata": {"name": "rerank eval seed", "version": "v1", "description": "real public seed",
                         "is_template": False},
            "record_count": 50,
            "set_hash": "sha256:" + hashlib.sha256("|".join(case_hashes).encode("utf-8")).hexdigest(),
            "cases": cases,
            "corpus": [
                {"id": "a", "title": "doc-a", "text": "alpha content"},
                {"id": "b", "title": "doc-b", "text": "beta content"},
            ],
        }
        report = server.build_stub_rrf_rerank_eval_report(dataset)
        self.assertTrue(report["ran"])
        self.assertFalse(report["refused"])
        self.assertEqual(report["provider_evidence"], "local_stub")
        self.assertFalse(report["is_real_rerank_provider_evidence"])
        self.assertEqual(report["case_count"], 50)
        json.dumps(report, ensure_ascii=False)


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
