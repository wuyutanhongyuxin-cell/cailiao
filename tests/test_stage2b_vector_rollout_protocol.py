"""Stage 2B vector rollout protocol tests."""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_VECTOR_ROLLOUT_PROTOCOL.md"
EXAMPLE = ROOT / "examples" / "stage2b_vector_rollout.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _complete_packet():
    return {
        "rollout_id": "vector-rollout-001",
        "rollout_mode": "canary",
        "vector_config": {
            "provider": {"provider": "openai", "model": "text-embedding-3-large",
                         "dim": 3072, "credential_source": "OPENAI_API_KEY"},
            "store": {"backend": "qdrant", "backup_configured": True,
                      "persistence_path": "managed persistent cluster"},
            "index": {"type": "hnsw", "metric": "cosine", "dim": 3072},
        },
        "index_manifest": {
            "manifest_id": "idx-001",
            "source_corpus_version": "corpus-2026-07",
            "chunker_version": "chunker-v1",
            "embedding_model": "text-embedding-3-large",
            "embedding_dim": 3072,
            "distance_metric": "cosine",
            "store_collection": "materials_v1_shadow",
            "build_command": "python tools/build_vector_index.py --manifest idx.json",
            "rebuild_command": "python tools/build_vector_index.py --rebuild --manifest idx.json",
            "rollback_plan": "disable vector channel and restore previous alias",
        },
        "migration": {
            "preflight_checklist": ["credential source configured", "backup tested"],
            "cutover_steps": ["build shadow index", "enable canary"],
            "rollback_steps": ["disable vector", "restore alias"],
        },
        "observability": {
            "metrics": ["latency_p50", "latency_p95", "error_rate", "recall@k"],
        },
        "acceptance": {
            "gates": ["recall@10 threshold met", "latency budget met"],
            "rollback_trigger": "quality or latency regression",
        },
    }


class VectorRolloutProtocolTest(unittest.TestCase):
    def test_default_not_ready_and_no_parent_check(self):
        r = server.build_stage2b_vector_rollout_protocol()
        self.assertFalse(r["ready_for_vector_rollout"])
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertFalse(r["current_shipped_state"]["production_ready"])

    def test_example_exists_and_is_not_ready(self):
        self.assertTrue(EXAMPLE.exists())
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertFalse(server.validate_stage2b_vector_rollout_packet(data)["ready"])

    def test_complete_packet_validates_shape_only(self):
        v = server.validate_stage2b_vector_rollout_packet(_complete_packet())
        self.assertTrue(v["passed"], v["errors"])
        self.assertTrue(v["ready"])
        self.assertFalse(v["roadmap_parent_items_checked"])

    def test_dim_mismatch_fails(self):
        packet = _complete_packet()
        packet["index_manifest"]["embedding_dim"] = 1024
        v = server.validate_stage2b_vector_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("dim" in e for e in v["errors"]))

    def test_metric_mismatch_fails(self):
        packet = _complete_packet()
        packet["index_manifest"]["distance_metric"] = "l2"
        v = server.validate_stage2b_vector_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("metric" in e for e in v["errors"]))

    def test_secret_shaped_field_fails(self):
        packet = _complete_packet()
        packet["vector_config"]["api_key"] = "sk-not-allowed"
        v = server.validate_stage2b_vector_rollout_packet(packet)
        self.assertFalse(v["ready"])
        self.assertTrue(any("credential" in e for e in v["errors"]))

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_vector_rollout_protocol({"vector_rollout": _complete_packet()}),
                   ensure_ascii=False)

    def test_doc_states_boundary(self):
        text = DOC.read_text(encoding="utf-8").lower()
        self.assertIn("does not call providers", text)
        self.assertIn("roadmap line 103", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_vector_rollout_protocol",
            ROOT / "tools" / "check_stage2b_vector_rollout_protocol.py")
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
