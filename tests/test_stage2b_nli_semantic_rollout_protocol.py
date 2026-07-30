"""Stage 2B/3 NLI semantic rollout protocol tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_NLI_SEMANTIC_ROLLOUT_PROTOCOL.md"
EXAMPLE = ROOT / "examples" / "stage2b_nli_semantic_rollout.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _complete_packet():
    return {
        "rollout_id": "nli-rollout-001",
        "rollout_mode": "canary",
        "semantic_config": {
            "provider": {"provider": "huggingface", "model": "microsoft/deberta-base-mnli",
                         "credential_source": "HF_TOKEN"},
            "eval_labels": ["entailment", "contradiction", "neutral"],
            "policy": {"verdict_labels": ["supports", "refutes", "not_enough_info"],
                       "min_confidence": 0.7, "block_on": ["refutes"], "warn_on": ["not_enough_info"]},
        },
        "label_mapping": {"entailment": "supports", "contradiction": "refutes",
                          "neutral": "not_enough_info"},
        "policy": {"verdict_labels": ["supports", "refutes", "not_enough_info"],
                   "min_confidence": 0.7, "block_on": ["refutes"], "warn_on": ["not_enough_info"]},
        "evidence_requirements": {
            "required_fields": ["claim_text", "cited_chunk_ids", "context_window", "provenance"],
            "min_context_window_chars": 400,
        },
        "eval_packet": {
            "dataset_readiness_status": "ready_real",
            "run_manifest_ref": "semantic-eval-001",
            "required_metrics": [
                "precision_by_label", "recall_by_label", "f1_by_label", "confusion_matrix",
                "calibration_notes", "abstention_rate", "refusal_rate",
            ],
        },
        "human_review": {
            "review_queue": "semantic-review",
            "escalation_triggers": ["low confidence", "refutes high impact claim"],
        },
        "observability": {
            "metrics": ["provider_error_rate", "latency_p95", "verdict_distribution", "escalation_rate"],
        },
        "rollout": {
            "preflight_checklist": ["credential source configured", "review queue staffed"],
            "canary_steps": ["shadow score", "enable canary"],
            "rollback_steps": ["disable semantic gate", "restore lexical detector"],
            "rollback_trigger": "quality or latency regression",
        },
    }


class NliSemanticRolloutProtocolTest(unittest.TestCase):
    def test_default_not_ready_and_no_parent_check(self):
        r = server.build_stage2b_nli_semantic_rollout_protocol()
        self.assertFalse(r["ready_for_semantic_rollout"])
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertFalse(r["current_shipped_state"]["does_semantic_entailment"])

    def test_example_exists_and_is_not_ready(self):
        self.assertTrue(EXAMPLE.exists())
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertFalse(server.validate_stage2b_nli_semantic_rollout_packet(data)["ready"])

    def test_complete_packet_validates_shape_only(self):
        v = server.validate_stage2b_nli_semantic_rollout_packet(_complete_packet())
        self.assertTrue(v["passed"], v["errors"])
        self.assertTrue(v["ready"])
        self.assertFalse(v["roadmap_parent_items_checked"])

    def test_missing_label_coverage_fails(self):
        packet = _complete_packet()
        del packet["label_mapping"]["neutral"]
        v = server.validate_stage2b_nli_semantic_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("label_mapping" in e for e in v["errors"]))

    def test_bad_policy_fails(self):
        packet = _complete_packet()
        packet["policy"]["min_confidence"] = 2.0
        v = server.validate_stage2b_nli_semantic_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("min_confidence" in e for e in v["errors"]))

    def test_missing_evidence_field_fails(self):
        packet = _complete_packet()
        packet["evidence_requirements"]["required_fields"].remove("provenance")
        v = server.validate_stage2b_nli_semantic_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("provenance" in e for e in v["errors"]))

    def test_missing_eval_metric_fails(self):
        packet = _complete_packet()
        packet["eval_packet"]["required_metrics"].remove("confusion_matrix")
        v = server.validate_stage2b_nli_semantic_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("confusion_matrix" in e for e in v["errors"]))

    def test_secret_shaped_field_fails(self):
        packet = _complete_packet()
        packet["semantic_config"]["api_key"] = "not-allowed"
        v = server.validate_stage2b_nli_semantic_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("credential" in e for e in v["errors"]))

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_nli_semantic_rollout_protocol(
            {"nli_semantic_rollout": _complete_packet()}), ensure_ascii=False)

    def test_doc_states_boundary(self):
        text = DOC.read_text(encoding="utf-8").lower()
        self.assertIn("does not call providers", text)
        self.assertIn("roadmap line 114", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_nli_semantic_rollout_protocol",
            ROOT / "tools" / "check_stage2b_nli_semantic_rollout_protocol.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_example_exits_one(self):
        self.assertEqual(self._run(["--config", str(EXAMPLE), "--json"]), 1)

    def test_cli_complete_temp_packet_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_packet(), f, ensure_ascii=False)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
