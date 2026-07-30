"""Aggregate Stage 2B rollout protocol status tests."""

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


def _vector_packet():
    from tests.test_stage2b_vector_rollout_protocol import _complete_packet
    return _complete_packet()


def _rerank_packet():
    from tests.test_stage2b_rerank_rollout_protocol import _complete_packet
    return _complete_packet()


def _nli_packet():
    from tests.test_stage2b_nli_semantic_rollout_protocol import _complete_packet
    return _complete_packet()


def _complete_config():
    return {"rollout_protocols": {
        "vector": _vector_packet(),
        "rerank": _rerank_packet(),
        "nli_semantic": _nli_packet(),
    }}


class RolloutProtocolsStatusTest(unittest.TestCase):
    def test_default_not_ready_and_no_parent_check(self):
        r = server.build_stage2b_rollout_protocols_status()
        self.assertFalse(r["all_rollout_protocols_ready"])
        self.assertEqual(set(r["outstanding_ids"]), {"vector", "rerank", "nli_semantic"})
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_complete_declared_packets_ready_shape_only(self):
        r = server.build_stage2b_rollout_protocols_status(_complete_config())
        self.assertTrue(r["all_rollout_protocols_ready"], r["outstanding_ids"])
        self.assertEqual(set(r["ready_ids"]), {"vector", "rerank", "nli_semantic"})
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertIn("metadata", r["boundary"])

    def test_template_examples_remain_not_ready(self):
        cfg = {"rollout_protocols": {
            "vector": json.loads((ROOT / "examples" / "stage2b_vector_rollout.example.json").read_text("utf-8")),
            "rerank": json.loads((ROOT / "examples" / "stage2b_rerank_rollout.example.json").read_text("utf-8")),
            "nli_semantic": json.loads((ROOT / "examples" / "stage2b_nli_semantic_rollout.example.json").read_text("utf-8")),
        }}
        r = server.build_stage2b_rollout_protocols_status(cfg)
        self.assertFalse(r["all_rollout_protocols_ready"])
        self.assertEqual(set(r["outstanding_ids"]), {"vector", "rerank", "nli_semantic"})

    def test_external_audit_mentions_rollout_protocols(self):
        a = server.build_external_dependency_audit()
        by_id = {b["id"]: b for b in a["blockers"]}
        self.assertIn("build_stage2b_vector_rollout_protocol", by_id["real_embedding_provider_vector_store"]["protected_by"])
        self.assertIn("build_stage2b_rerank_rollout_protocol", by_id["real_reranker_rrf"]["protected_by"])
        self.assertIn("build_stage2b_nli_semantic_rollout_protocol", by_id["real_nli_semantic_conflict"]["protected_by"])
        self.assertEqual(by_id["real_reranker_rrf"]["roadmap_line"], 107)
        self.assertEqual(by_id["real_nli_semantic_conflict"]["roadmap_line"], 114)

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_rollout_protocols_status(), ensure_ascii=False)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_rollout_protocols_status",
            ROOT / "tools" / "check_stage2b_rollout_protocols_status.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_complete_temp_config_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_config(), f, ensure_ascii=False)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
