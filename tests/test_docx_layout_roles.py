"""Stage 4 deterministic DOCX layout role tests."""

import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class DocxLayoutRolesTest(unittest.TestCase):
    def test_layout_plan_detects_title_heading_and_body(self):
        plan = server.build_docx_layout_plan("材料标题", "一、总体要求\n\n正文段落\n\n（一）重点任务")
        self.assertEqual(plan["method"], "docx_layout_plan_v1")
        self.assertEqual([p["role"] for p in plan["paragraphs"]], ["title", "heading", "body", "heading"])
        self.assertEqual(plan["summary"]["heading_count"], 2)
        self.assertEqual(plan["summary"]["body_count"], 1)

    def test_signature_and_imprint_are_appended_only_when_provided(self):
        empty = server.build_docx_layout_plan("标题", "正文")
        self.assertFalse(empty["signature_enabled"])
        self.assertFalse(empty["imprint_enabled"])
        self.assertEqual([p["role"] for p in empty["paragraphs"]], ["title", "body"])

        plan = server.build_docx_layout_plan("标题", "正文", {
            "signature": "办公室\n2026年7月29日",
            "imprint": {"organization": "办公室", "copies": "10份"},
        })
        self.assertEqual([p["role"] for p in plan["paragraphs"]], ["title", "body", "signature", "imprint"])
        self.assertTrue(plan["signature_enabled"])
        self.assertTrue(plan["imprint_enabled"])

    def test_export_docx_uses_heading_style_for_detected_headings(self):
        raw = server.export_docx("标题", "一、总体要求\n\n正文段落")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn('<w:pStyle w:val="MaterialTitle"/>', document)
        self.assertIn('<w:pStyle w:val="MaterialHeading"/>', document)
        self.assertIn('<w:pStyle w:val="MaterialBody"/>', document)

    def test_footer_part_and_relationship_present_by_default(self):
        raw = server.export_docx("标题", "正文")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            footer = z.read("word/footer1.xml").decode("utf-8")
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn("word/footer1.xml", names)
        self.assertIn(" PAGE ", footer)
        self.assertIn("relationships/footer", rels)
        self.assertIn("footerReference", document)

    def test_footer_can_be_disabled(self):
        raw = server.export_docx("标题", "正文", {"page_number": False})
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
            document = z.read("word/document.xml").decode("utf-8")
        self.assertNotIn("word/footer1.xml", names)
        self.assertNotIn("relationships/footer", rels)
        self.assertNotIn("footerReference", document)

    def test_layout_plan_is_json_serializable_and_export_compatible(self):
        plan = server.build_docx_layout_plan("标题", "1. 任务\n\n正文", {
            "style_profile": {"signature": "办公室"},
        })
        json.dumps(plan, ensure_ascii=False)
        raw = server.export_docx("标题", "1. 任务\n\n正文", {"signature": "办公室"})
        self.assertGreater(len(raw), 1000)


if __name__ == "__main__":
    unittest.main()
