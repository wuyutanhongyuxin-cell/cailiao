"""Stage 2B reranker rollout protocol tests."""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_RERANK_ROLLOUT_PROTOCOL.md"
EXAMPLE = ROOT / "examples" / "stage2b_rerank_rollout.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _complete_packet():
    return {
        "rollout_id": "rerank-rollout-001",
        "rollout_mode": "canary",
        "rerank_config": {
            "provider": {"provider": "cohere", "model": "rerank-3.5",
                         "credential_source": "COHERE_API_KEY",
                         "eval_metrics": ["mrr", "ndcg", "map"]},
            "rrf": {"rank_constant": 60, "rank_window_size": 100,
                    "channels": ["bm25", "vector", "rerank"]},
        },
        "candidate_policy": {
            "top_k": 50,
            "rerank_only_fused_top_k": True,
            "may_retrieve_new_chunks": False,
        },
        "rrf_policy": {
            "rank_constant": 60,
            "rank_window_size": 100,
            "channels": ["bm25", "vector", "rerank"],
            "tie_policy": "stable_id",
        },
        "eval_packet": {
            "dataset_readiness_status": "ready_real",
            "run_manifest_ref": "eval-run-001",
            "required_metrics": ["mrr", "ndcg", "map", "recall@k", "latency_p95"],
        },
        "observability": {
            "metrics": ["latency_p50", "latency_p95", "provider_error_rate", "rerank_invocations"],
        },
        "rollout": {
            "preflight_checklist": ["credential source configured", "shadow mode enabled"],
            "canary_steps": ["enable shadow", "enable canary"],
            "rollback_steps": ["disable rerank", "restore RRF config"],
            "rollback_trigger": "latency or quality regression",
        },
    }


class RerankRolloutProtocolTest(unittest.TestCase):
    def test_default_not_ready_and_no_parent_check(self):
        r = server.build_stage2b_rerank_rollout_protocol()
        self.assertFalse(r["ready_for_rerank_rollout"])
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertFalse(r["current_shipped_state"]["is_real_rerank_model"])

    def test_example_exists_and_is_not_ready(self):
        self.assertTrue(EXAMPLE.exists())
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertFalse(server.validate_stage2b_rerank_rollout_packet(data)["ready"])

    def test_complete_packet_validates_shape_only(self):
        v = server.validate_stage2b_rerank_rollout_packet(_complete_packet())
        self.assertTrue(v["passed"], v["errors"])
        self.assertTrue(v["ready"])
        self.assertFalse(v["roadmap_parent_items_checked"])

    def test_rerank_must_not_retrieve_new_chunks(self):
        packet = _complete_packet()
        packet["candidate_policy"]["may_retrieve_new_chunks"] = True
        v = server.validate_stage2b_rerank_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("may_retrieve_new_chunks" in e for e in v["errors"]))

    def test_missing_eval_metric_fails(self):
        packet = _complete_packet()
        packet["eval_packet"]["required_metrics"].remove("latency_p95")
        v = server.validate_stage2b_rerank_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("latency_p95" in e for e in v["errors"]))

    def test_bad_rrf_policy_fails(self):
        packet = _complete_packet()
        packet["rrf_policy"]["rank_constant"] = 0
        v = server.validate_stage2b_rerank_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("rank_constant" in e for e in v["errors"]))

    def test_secret_shaped_field_fails(self):
        packet = _complete_packet()
        packet["rerank_config"]["token"] = "not-allowed"
        v = server.validate_stage2b_rerank_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("credential" in e for e in v["errors"]))

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_rerank_rollout_protocol({"rerank_rollout": _complete_packet()}),
                   ensure_ascii=False)

    def test_doc_states_boundary(self):
        text = DOC.read_text(encoding="utf-8").lower()
        self.assertIn("does not call providers", text)
        self.assertIn("roadmap line 107", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_rerank_rollout_protocol",
            ROOT / "tools" / "check_stage2b_rerank_rollout_protocol.py")
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
