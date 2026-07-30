"""Stage 3 real NLI / semantic-conflict production-readiness scaffold tests.

Honesty guard: no real NLI/LLM model exists in this repo. Tests build DECLARED
configs in memory to exercise the readiness gate and the deterministic label
mapper; a "ready" verdict means the config is complete, never that inference ran.
The shipped conflict detector stays deterministic-lexical.
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
