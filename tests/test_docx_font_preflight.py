"""Stage 4 deterministic DOCX font fallback / export preflight tests."""

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


class DocxFontFallbackPlanTest(unittest.TestCase):
    def test_default_fonts_are_all_known(self):
        plan = server.build_font_fallback_plan()
        self.assertEqual(plan["method"], "docx_font_fallback_plan_v1")
        self.assertEqual(plan["summary"]["role_count"], 4)
        self.assertEqual(plan["summary"]["unknown_count"], 0)
        self.assertEqual(plan["summary"]["warning_count"], 0)
        self.assertTrue(all(role["is_known"] for role in plan["roles"]))

    def test_unknown_font_flagged_with_warning_but_still_reported(self):
        plan = server.build_font_fallback_plan({"font_family": "MadeUpFont"})
        body_role = next(r for r in plan["roles"] if r["role"] == "body")
        self.assertEqual(body_role["requested"], "MadeUpFont")
        self.assertFalse(body_role["is_known"])
        self.assertEqual(plan["summary"]["unknown_count"], 1)
        self.assertEqual(plan["summary"]["warning_count"], 1)
        self.assertTrue(any("MadeUpFont" in w for w in plan["warnings"]))

    def test_fallback_candidates_exclude_requested_and_dedupe(self):
        plan = server.build_font_fallback_plan({"font_family": "FangSong"})
        body_role = next(r for r in plan["roles"] if r["role"] == "body")
        self.assertNotIn("FangSong", body_role["fallback_candidates"])
        self.assertEqual(
            len(body_role["fallback_candidates"]),
            len(set(body_role["fallback_candidates"])),
        )
        self.assertIn("SimSun", body_role["fallback_candidates"])

    def test_known_font_list_size_is_positive(self):
        plan = server.build_font_fallback_plan()
        self.assertGreater(plan["known_font_list_size"], 0)

    def test_plan_is_json_serializable(self):
        json.dumps(server.build_font_fallback_plan({"title_font": "X"}), ensure_ascii=False)


class DocxExportPreflightReportTest(unittest.TestCase):
    def test_report_aggregates_all_sections(self):
        report = server.build_export_preflight_report("标题", "一、正文段落")
        self.assertEqual(report["method"], "docx_export_preflight_v1")
        self.assertEqual(report["version"], "docx_export_preflight_v1")
        self.assertIn("font_fallback_plan", report)
        self.assertIn("layout_plan_summary", report)
        self.assertIn("structured_field_summary", report)
        self.assertIsInstance(report["export_boundary_warnings"], list)
        self.assertGreater(len(report["export_boundary_warnings"]), 0)

    def test_summary_counts_reflect_inputs(self):
        report = server.build_export_preflight_report(
            "标题",
            "一、第一部分\n\n二、第二部分",
            {
                "attachments": ["任务清单"],
                "tables": [{"headers": ["事项", "时限"], "rows": [["导入", "7月"]]}],
            },
        )
        self.assertEqual(report["summary"]["attachment_count"], 1)
        self.assertEqual(report["summary"]["table_count"], 1)
        self.assertGreaterEqual(report["summary"]["paragraph_count"], 3)
        self.assertEqual(report["summary"]["font_role_count"], 4)

    def test_unknown_font_propagates_into_boundary_warnings(self):
        report = server.build_export_preflight_report(
            "标题", "正文", {"font_family": "MadeUpFont"}
        )
        self.assertEqual(report["summary"]["unknown_font_count"], 1)
        self.assertTrue(any("MadeUpFont" in w for w in report["export_boundary_warnings"]))

    def test_report_is_json_serializable(self):
        json.dumps(
            server.build_export_preflight_report("标题", "正文", {"issuer": "张三"}),
            ensure_ascii=False,
        )

    def test_export_docx_output_unchanged_by_additive_helpers(self):
        # Additive-only guarantee: exporting still produces a valid DOCX package.
        raw = server.export_docx("标题", "正文")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn("word/document.xml", z.namelist())
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn("标题", document)
        self.assertIn("正文", document)


if __name__ == "__main__":
    unittest.main()
