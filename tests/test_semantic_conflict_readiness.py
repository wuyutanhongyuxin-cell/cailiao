"""Stage 3 real NLI / semantic-conflict production-readiness scaffold tests.

Honesty guard: no real NLI/LLM model exists in this repo. Tests build DECLARED
configs in memory to exercise the readiness gate and the deterministic label
mapper; a "ready" verdict means the config is complete, never that inference ran.
The shipped conflict detector stays deterministic-lexical.
"""

import contextlib
import http.server
import importlib.util
import io
import json
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
        "provider": {"provider": "huggingface", "model": "microsoft/deberta-base-mnli",
                     "credential_source": "HF_TOKEN"},
        "eval_labels": ["entailment", "contradiction", "neutral"],
        "policy": {"verdict_labels": ["supports", "refutes", "not_enough_info"],
                   "min_confidence": 0.7, "block_on": ["refutes"], "warn_on": ["not_enough_info"]},
    }


class LabelMapperTest(unittest.TestCase):
    def test_maps_snli_and_fever_labels(self):
        self.assertEqual(server.map_nli_label_to_verdict("entailment"), "supports")
        self.assertEqual(server.map_nli_label_to_verdict("supports"), "supports")
        self.assertEqual(server.map_nli_label_to_verdict("contradiction"), "refutes")
        self.assertEqual(server.map_nli_label_to_verdict("refutes"), "refutes")
        self.assertEqual(server.map_nli_label_to_verdict("neutral"), "not_enough_info")
        self.assertEqual(server.map_nli_label_to_verdict("NEI"), "not_enough_info")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(server.map_nli_label_to_verdict("  Entailment  "), "supports")

    def test_unknown_label_raises(self):
        with self.assertRaises(ValueError):
            server.map_nli_label_to_verdict("maybe")

    def test_semantic_eval_verdict_aliases_include_abstain(self):
        self.assertEqual(server.normalize_semantic_eval_verdict("entailment"), "entailment")
        self.assertEqual(server.normalize_semantic_eval_verdict("supports"), "entailment")
        self.assertEqual(server.normalize_semantic_eval_verdict("contradiction"), "contradiction")
        self.assertEqual(server.normalize_semantic_eval_verdict("neutral"), "neutral")
        self.assertEqual(server.normalize_semantic_eval_verdict("abstention"), "abstain")


class NliProviderReadinessTest(unittest.TestCase):
    def test_default_is_lexical_not_real(self):
        r = server.build_nli_provider_readiness()
        self.assertTrue(r["is_lexical_only"])
        self.assertFalse(r["is_real_provider_declared"])

    def test_lexical_mode_not_real(self):
        r = server.build_nli_provider_readiness({"mode": "lexical"})
        self.assertFalse(r["is_real_provider_declared"])

    def test_real_provider_needs_model_and_credential_source(self):
        r = server.build_nli_provider_readiness({"provider": "huggingface"})
        self.assertFalse(r["is_real_provider_declared"])
        self.assertTrue(r["missing"])

    def test_complete_real_provider_declared(self):
        r = server.build_nli_provider_readiness(_complete_config()["provider"])
        self.assertTrue(r["is_real_provider_declared"])
        self.assertEqual(r["missing"], [])

    def test_credential_value_rejected_source_accepted(self):
        bad = server.validate_nli_provider_config(
            {"provider": "huggingface", "model": "m", "api_key": "sk"})
        self.assertFalse(bad["passed"])
        ok = server.validate_nli_provider_config(_complete_config()["provider"])
        self.assertTrue(ok["passed"], ok["errors"])

    def test_http_semantic_provider_requires_endpoint(self):
        r = server.build_nli_provider_readiness({
            "provider": "http_semantic_judge_compatible",
            "model": "semantic-test",
            "credential_source": "SEMANTIC_API_KEY",
        })
        self.assertFalse(r["is_real_provider_declared"])
        self.assertTrue(any("endpoint_url" in m for m in r["missing"]))

    def test_http_semantic_provider_declared_with_endpoint(self):
        r = server.build_nli_provider_readiness({
            "provider": "http_semantic_judge_compatible",
            "endpoint_url": "https://example.test/semantic",
            "model": "semantic-test",
            "credential_source": "SEMANTIC_API_KEY",
        })
        self.assertTrue(r["is_real_provider_declared"], r["missing"])
        self.assertEqual(r["endpoint_host"], "example.test")


class HTTPSemanticJudgeProviderTest(unittest.TestCase):
    def _serve_once(self):
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                seen["path"] = self.path
                seen["authorization"] = self.headers.get("Authorization")
                seen["body"] = json.loads(body)
                payload = json.dumps({"verdict": "contradiction", "confidence": 0.91}).encode("utf-8")
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

    def test_local_stub_request_shape_and_no_key_leak(self):
        httpd, seen = self._serve_once()
        old = os.environ.get("TEST_SEMANTIC_KEY")
        os.environ["TEST_SEMANTIC_KEY"] = "secret-semantic-key"
        try:
            provider = server.HTTPSemanticJudgeProvider(
                endpoint_url=f"http://127.0.0.1:{httpd.server_address[1]}/v1/semantic-judge",
                model="semantic-test",
                credential_source="TEST_SEMANTIC_KEY",
                timeout_sec=5,
            )
            out = provider.judge("claim text", "evidence text")
            self.assertEqual(out, {"verdict": "contradiction", "confidence": 0.91})
            self.assertEqual(seen["path"], "/v1/semantic-judge")
            self.assertEqual(seen["authorization"], "Bearer secret-semantic-key")
            self.assertEqual(seen["body"]["model"], "semantic-test")
            self.assertEqual(seen["body"]["claim"], "claim text")
            self.assertEqual(seen["body"]["evidence"], "evidence text")
            self.assertEqual(seen["body"]["labels"], list(server.SEMANTIC_EVAL_VERDICTS))
            meta = provider.metadata()
            self.assertTrue(meta["connectivity_proven"])
            self.assertTrue(meta["does_semantic_entailment"])
            self.assertNotIn("secret-semantic-key", json.dumps(meta))
        finally:
            httpd.shutdown()
            httpd.server_close()
            if old is None:
                os.environ.pop("TEST_SEMANTIC_KEY", None)
            else:
                os.environ["TEST_SEMANTIC_KEY"] = old

    def test_missing_env_var_fails_without_secret_value(self):
        os.environ.pop("MISSING_SEMANTIC_KEY", None)
        provider = server.HTTPSemanticJudgeProvider(
            endpoint_url="http://127.0.0.1:9/v1/semantic-judge",
            model="semantic-test",
            credential_source="MISSING_SEMANTIC_KEY",
            timeout_sec=1,
        )
        with self.assertRaises(RuntimeError) as ctx:
            provider.judge("claim", "evidence")
        self.assertIn("MISSING_SEMANTIC_KEY", str(ctx.exception))
        self.assertNotIn("Bearer", str(ctx.exception))

    def test_placeholder_credential_source_rejected(self):
        with self.assertRaises(ValueError):
            server.HTTPSemanticJudgeProvider(
                endpoint_url="http://127.0.0.1:9/v1/semantic-judge",
                model="semantic-test",
                credential_source="sk-secret-value",
            )


class SemanticPolicyTest(unittest.TestCase):
    def test_default_policy_valid(self):
        policy = server.build_semantic_conflict_policy()
        self.assertEqual(policy["block_on"], ["refutes"])
        self.assertTrue(server.validate_semantic_conflict_policy(policy)["passed"])

    def test_bad_threshold_rejected(self):
        self.assertFalse(server.validate_semantic_conflict_policy(
            {"verdict_labels": ["supports"], "min_confidence": 2.0})["passed"])

    def test_unknown_verdict_label_rejected(self):
        self.assertFalse(server.validate_semantic_conflict_policy(
            {"verdict_labels": ["supports", "mystery"], "min_confidence": 0.5})["passed"])


class SemanticConflictReadinessTest(unittest.TestCase):
    def test_default_repo_state_not_semantic_ready(self):
        r = server.build_semantic_conflict_readiness()
        self.assertFalse(r["production_ready"])
        self.assertTrue(r["missing"])
        self.assertFalse(r["current_shipped_state"]["production_ready"])
        self.assertFalse(r["current_shipped_state"]["is_real_nli_model"])
        self.assertFalse(r["current_shipped_state"]["does_semantic_entailment"])

    def test_complete_declared_config_ready_without_network(self):
        r = server.build_semantic_conflict_readiness(_complete_config())
        self.assertTrue(r["production_ready"], r["missing"])
        self.assertEqual(r["missing"], [])

    def test_missing_eval_labels_not_ready(self):
        cfg = _complete_config()
        cfg["eval_labels"] = ["entailment"]  # only 'supports' covered
        r = server.build_semantic_conflict_readiness(cfg)
        self.assertFalse(r["production_ready"])
        self.assertTrue(any("eval labels" in m for m in r["missing"]))

    def test_real_provider_but_bad_policy_not_ready(self):
        cfg = _complete_config()
        cfg["policy"] = {"verdict_labels": ["supports"], "min_confidence": 5.0}
        r = server.build_semantic_conflict_readiness(cfg)
        self.assertFalse(r["production_ready"])
        self.assertTrue(any("policy" in m for m in r["missing"]))

    def test_lexical_provider_never_ready(self):
        cfg = _complete_config()
        cfg["provider"] = {"mode": "lexical"}
        r = server.build_semantic_conflict_readiness(cfg)
        self.assertFalse(r["production_ready"])

    def test_readiness_is_json_serializable(self):
        json.dumps(server.build_semantic_conflict_readiness(_complete_config()), ensure_ascii=False)


class StubSemanticEvalReportTest(unittest.TestCase):
    def test_report_has_confusion_matrix_metrics_and_stub_boundary(self):
        cases = [
            {"id": "e1", "claim": "A", "evidence": "A", "expected_verdict": "entailment",
             "predicted_verdict": "entailment"},
            {"id": "c1", "claim": "A", "evidence": "not A", "expected_verdict": "contradiction",
             "predicted_verdict": "contradiction"},
            {"id": "n1", "claim": "A", "evidence": "unknown", "expected_verdict": "neutral",
             "predicted_verdict": "abstain"},
            {"id": "a1", "claim": "A", "evidence": "", "expected_verdict": "abstain",
             "predicted_verdict": "abstain"},
        ]
        report = server.build_stub_semantic_eval_report(cases)
        self.assertTrue(report["ran"])
        self.assertFalse(report["refused"])
        self.assertEqual(report["provider_evidence"], "local_stub")
        self.assertFalse(report["is_real_nli_provider_evidence"])
        self.assertEqual(report["case_count"], 4)
        self.assertIn("confusion_matrix", report)
        self.assertEqual(report["confusion_matrix"]["neutral"]["abstain"], 1)
        self.assertIn("f1", report["metrics_by_label"]["entailment"])
        json.dumps(report, ensure_ascii=False)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_semantic_conflict_readiness", ROOT / "tools" / "check_semantic_conflict_readiness.py")
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
