"""Stage 4 deterministic DOCX render/layout regression tests.

Structural/markup regression only. No visual renderer is bundled or invoked;
these tests assert on OOXML package parts and layout invariants.
"""

import importlib.util
import json
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class DocxPackageInspectorTest(unittest.TestCase):
    def test_inspects_default_package_parts_and_page(self):
        raw = server.export_docx("标题", "一、正文段落")
        info = server.inspect_docx_package_layout(raw)
        self.assertEqual(info["method"], "docx_package_layout_inspector_v1")
        self.assertTrue(info["readable_zip"])
        self.assertTrue(info["has_document"])
        self.assertTrue(info["has_styles"])
        self.assertIn("word/document.xml", info["parts"])
        self.assertEqual(info["page"]["width_twips"], 11906)
        self.assertEqual(info["page"]["height_twips"], 16838)
        self.assertTrue(info["page"]["has_margins"])
        self.assertEqual(info["page"]["margins_twips"]["top"], 2126)

    def test_footer_present_and_page_field_when_enabled(self):
        raw = server.export_docx("标题", "正文")
        info = server.inspect_docx_package_layout(raw)
        self.assertTrue(info["has_footer"])
        self.assertTrue(info["footer_has_page_field"])

    def test_footer_absent_when_page_number_disabled(self):
        raw = server.export_docx("标题", "正文", {"page_number": False})
        info = server.inspect_docx_package_layout(raw)
        self.assertFalse(info["has_footer"])
        self.assertFalse(info["footer_has_page_field"])

    def test_style_references_capture_used_styles(self):
        raw = server.export_docx("标题", "一、层级标题")
        info = server.inspect_docx_package_layout(raw)
        self.assertIn("MaterialTitle", info["style_references"])
        self.assertIn("MaterialHeading", info["style_references"])

    def test_invalid_bytes_return_safe_unreadable_result(self):
        info = server.inspect_docx_package_layout(b"not-a-zip")
        self.assertFalse(info["readable_zip"])
        self.assertFalse(info["has_document"])
        self.assertEqual(info["parts"], [])


class DocxLayoutRegressionReportTest(unittest.TestCase):
    def test_default_report_passes_all_checks(self):
        report = server.build_docx_layout_regression_report("标题", "一、正文")
        self.assertEqual(report["method"], "docx_layout_regression_v1")
        self.assertTrue(report["passed"], report["failed_checks"])
        self.assertEqual(report["summary"]["failed_count"], 0)
        self.assertGreater(report["summary"]["check_count"], 0)
        names = {c["name"] for c in report["checks"]}
        self.assertIn("document_present", names)
        self.assertIn("styles_present", names)
        self.assertIn("page_size_present", names)

    def test_report_reflects_structured_and_font_counts(self):
        report = server.build_docx_layout_regression_report(
            "标题",
            "正文",
            {
                "font_family": "MadeUpFont",
                "attachments": ["任务清单"],
                "tables": [{"headers": ["事项", "时限"], "rows": [["导入", "7月"]]}],
            },
        )
        self.assertEqual(report["summary"]["table_count"], 1)
        self.assertEqual(report["summary"]["attachment_count"], 1)
        self.assertEqual(report["summary"]["unknown_font_count"], 1)
        # An unknown font is advisory only; it must not fail the layout regression.
        self.assertTrue(report["passed"], report["failed_checks"])

    def test_footer_check_tracks_page_number_flag(self):
        with_footer = server.build_docx_layout_regression_report("标题", "正文")
        without_footer = server.build_docx_layout_regression_report(
            "标题", "正文", {"page_number": False}
        )
        self.assertTrue(with_footer["passed"], with_footer["failed_checks"])
        self.assertTrue(without_footer["passed"], without_footer["failed_checks"])
        with_names = {c["name"] for c in with_footer["checks"]}
        without_names = {c["name"] for c in without_footer["checks"]}
        self.assertIn("footer_has_page_field", with_names)
        self.assertNotIn("footer_has_page_field", without_names)

    def test_report_is_json_serializable(self):
        json.dumps(
            server.build_docx_layout_regression_report("标题", "正文", {"issuer": "张三"}),
            ensure_ascii=False,
        )


if __name__ == "__main__":
    unittest.main()
