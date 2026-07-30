"""Stage 2B real-query collection protocol / de-identification gate tests.

Honesty guard: no real queries are collected here. The default and the placeholder
example are not ready-for-collection, PII-shaped samples are rejected (without
leaking the raw value), and nothing checks a ROADMAP parent.
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
PROTOCOL_DOC = ROOT / "docs" / "STAGE2B_REAL_QUERY_COLLECTION_PROTOCOL.md"
EXAMPLE_FILE = ROOT / "examples" / "real_query_collection_packet.example.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _complete_packet():
    return {
        "collector_role": "数据治理专员（按角色）",
        "collection_purpose": "收集真实匿名检索查询用于评测",
        "retention_policy": "原始素材定稿后安全销毁",
        "access_control_summary": "原始素材仅限收集/复核角色访问",
        "target_count": 60,
        "deidentification_checklist": {k: True for k in server.STAGE2B_DEID_CHECKLIST},
    }


class ProtocolDefaultTest(unittest.TestCase):
    def test_default_not_ready_no_parent_no_real(self):
        r = server.build_real_query_collection_protocol()
        self.assertFalse(r["ready_for_collection"])
        self.assertFalse(r["contains_real_queries"])
        self.assertFalse(r["roadmap_parent_items_checked"])
        self.assertEqual(r["target_case_range"], [50, 100])

    def test_exposes_checklist_forbidden_categories_references(self):
        r = server.build_real_query_collection_protocol()
        for item in ("direct_identifiers_removed", "reviewer_signoff"):
            self.assertIn(item, r["deidentification_checklist"])
        for cat in ("name", "phone", "email", "id_card"):
            self.assertIn(cat, r["forbidden_data_categories"])
        self.assertEqual(len(r["references"]), 4)

    def test_json_serializable(self):
        json.dumps(server.build_real_query_collection_protocol(), ensure_ascii=False)


class ExampleFileTest(unittest.TestCase):
    def test_example_parses_and_not_ready(self):
        self.assertTrue(EXAMPLE_FILE.exists())
        data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
        v = server.validate_real_query_collection_packet(data)
        self.assertFalse(v["ready"])
        self.assertTrue(v["errors"])

    def test_example_has_no_pii_shaped_values(self):
        text = EXAMPLE_FILE.read_text(encoding="utf-8")
        import re
        self.assertIsNone(re.search(r"\b1[3-9]\d{9}\b", text))       # no phone
        self.assertIsNone(re.search(r"\b\d{17}[\dXx]\b", text))       # no id card
        self.assertIsNone(re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text))  # no email


class PacketValidationTest(unittest.TestCase):
    def test_complete_packet_validates(self):
        v = server.validate_real_query_collection_packet(_complete_packet())
        self.assertTrue(v["passed"], v["errors"])
        self.assertTrue(v["ready"])

    def test_template_marker_fails(self):
        p = _complete_packet()
        p["is_template"] = True
        self.assertFalse(server.validate_real_query_collection_packet(p)["ready"])

    def test_missing_checklist_item_fails(self):
        p = _complete_packet()
        p["deidentification_checklist"]["reviewer_signoff"] = False
        v = server.validate_real_query_collection_packet(p)
        self.assertFalse(v["ready"])
        self.assertTrue(any("reviewer_signoff" in e for e in v["errors"]))

    def test_missing_control_field_fails(self):
        p = _complete_packet()
        p["access_control_summary"] = ""
        self.assertFalse(server.validate_real_query_collection_packet(p)["ready"])

    def test_target_count_out_of_band_fails(self):
        for tc in (10, 500):
            p = _complete_packet()
            p["target_count"] = tc
            self.assertFalse(server.validate_real_query_collection_packet(p)["ready"])

    def test_pii_shaped_sample_fails_without_leaking_value(self):
        p = _complete_packet()
        p["sample_cases"] = [{"id": "q1", "query": "请联系 13800138000 办理", "provenance": {}}]
        v = server.validate_real_query_collection_packet(p)
        self.assertFalse(v["ready"])
        self.assertTrue(any("PII-shaped" in e for e in v["errors"]))
        self.assertFalse(any("13800138000" in e for e in v["errors"]))

    def test_forbidden_field_sample_fails(self):
        p = _complete_packet()
        p["sample_cases"] = [{"id": "q1", "query": "正常查询", "email": "a@b.com", "provenance": {}}]
        self.assertFalse(server.validate_real_query_collection_packet(p)["ready"])

    def test_non_object_fails_safely(self):
        self.assertFalse(server.validate_real_query_collection_packet(["nope"])["passed"])


class ProtocolDocTest(unittest.TestCase):
    def test_doc_exists_and_states_boundary(self):
        self.assertTrue(PROTOCOL_DOC.exists())
        text = PROTOCOL_DOC.read_text(encoding="utf-8").lower()
        self.assertIn("no real dataset", text)
        self.assertIn("de-identification", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_real_query_collection_protocol", ROOT / "tools" / "check_real_query_collection_protocol.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_example_exits_one(self):
        self.assertEqual(self._run(["--config", str(EXAMPLE_FILE), "--json"]), 1)

    def test_cli_complete_temp_packet_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_packet(), f, ensure_ascii=False)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
