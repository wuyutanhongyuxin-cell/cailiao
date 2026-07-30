"""Stage 2B human action packet tests.

Honesty guard: this packet is a checklist for external human inputs. It must not
claim readiness by default or check real ROADMAP parent items.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
DOC = ROOT / "docs" / "STAGE2B_HUMAN_ACTION_PACKET.md"
EXAMPLE = ROOT / "examples" / "stage2b_human_action_packet.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

EXPECTED_IDS = [
    "real_query_set",
    "real_query_bm25_calibration",
    "real_embedding_provider_vector_store",
    "real_reranker_rrf",
    "real_nli_semantic_conflict",
]
EXPECTED_LINES = [97, 100, 103, 107, 114]


class HumanActionPacketHelperTest(unittest.TestCase):
    def test_default_lists_all_five_actions_as_outstanding(self):
        r = server.build_stage2b_human_action_packet()
        self.assertEqual(r["method"], "stage2b_human_action_packet_v1")
        self.assertEqual(r["action_item_count"], 5)
        self.assertEqual(r["outstanding_action_ids"], EXPECTED_IDS)
        self.assertFalse(r["all_human_actions_resolved"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_action_ids_and_lines_match_external_audit(self):
        r = server.build_stage2b_human_action_packet()
        self.assertEqual([a["id"] for a in r["action_items"]], EXPECTED_IDS)
        self.assertEqual([a["roadmap_line"] for a in r["action_items"]], EXPECTED_LINES)
        audit = server.build_external_dependency_audit()
        self.assertEqual([a["id"] for a in r["action_items"]], [b["id"] for b in audit["blockers"]])

    def test_each_action_has_acceptance_artifacts(self):
        r = server.build_stage2b_human_action_packet()
        for item in r["action_items"]:
            self.assertTrue(item["human_action_required"])
            self.assertGreaterEqual(len(item["acceptance_artifacts"]), 3)
            self.assertIn("required_external_input", item)
            self.assertIn("protected_by", item)

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_human_action_packet(), ensure_ascii=False)


class HumanActionPacketFilesTest(unittest.TestCase):
    def test_doc_and_example_exist(self):
        self.assertTrue(DOC.exists())
        self.assertTrue(EXAMPLE.exists())
        text = DOC.read_text(encoding="utf-8")
        for bid in EXPECTED_IDS:
            self.assertIn(bid, text)
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertTrue(data["is_template"])
        self.assertEqual(data["artifacts"], {})


class HumanActionPacketCliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_human_action_packet", ROOT / "tools" / "check_stage2b_human_action_packet.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_template_example_exits_one(self):
        self.assertEqual(self._run(["--config", str(EXAMPLE), "--json"]), 1)


if __name__ == "__main__":
    unittest.main()
