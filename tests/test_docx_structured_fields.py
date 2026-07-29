"""Stage 4 deterministic DOCX structured field tests."""

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


class DocxStructuredFieldsTest(unittest.TestCase):
    def test_normalizes_structured_fields_and_counts(self):
        fields = server.build_docx_structured_fields({
            "document_number": "材发〔2026〕1号",
            "issuer": "张三",
            "recipient": "各科室",
            "attachments": ["任务清单", {"name": "责任表"}, ""],
            "tables": [{"title": "进度表", "headers": ["事项", "时限"], "rows": [["导入", "7月"]]}],
        })
        self.assertEqual(fields["method"], "docx_structured_fields_v1")
        self.assertTrue(fields["summary"]["has_document_number"])
        self.assertEqual(fields["summary"]["attachment_count"], 2)
        self.assertEqual(fields["summary"]["table_count"], 1)
        self.assertEqual(fields["attachments"][1]["title"], "责任表")

    def test_export_without_structured_fields_stays_valid(self):
        raw = server.export_docx("标题", "正文")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn("word/document.xml", z.namelist())
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn("标题", document)
        self.assertIn("正文", document)

    def test_document_contains_escaped_metadata_fields(self):
        raw = server.export_docx("标题", "正文", {
            "document_number": "材发〔2026〕1&2号",
            "issuer": "张<三>",
            "recipient": "各科室&单位",
        })
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn("材发〔2026〕1&amp;2号", document)
        self.assertIn("签发人：张&lt;三&gt;", document)
        self.assertIn("主送机关：各科室&amp;单位", document)

    def test_attachments_render_as_deterministic_paragraphs(self):
        raw = server.export_docx("标题", "正文", {
            "attachments": ["任务清单", {"title": "责任分工"}],
            "signature": "办公室",
        })
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn("附件1：任务清单", document)
        self.assertIn("附件2：责任分工", document)
        self.assertLess(document.index("附件1：任务清单"), document.index("办公室"))

    def test_simple_table_writes_tbl_headers_and_rows(self):
        raw = server.export_docx("标题", "正文", {
            "tables": [{
                "title": "进度表",
                "headers": ["事项", "时限"],
                "rows": [["导入", "7月"], {"事项": "复核", "时限": "8月"}],
            }],
        })
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn("<w:tbl>", document)
        for text in ("进度表", "事项", "时限", "导入", "7月", "复核", "8月"):
            self.assertIn(text, document)

    def test_invalid_table_inputs_are_ignored_safely(self):
        fields = server.build_docx_structured_fields({
            "tables": "not-list",
            "attachments": "not-list",
        })
        self.assertEqual(fields["tables"], [])
        self.assertEqual(fields["attachments"], [])
        self.assertEqual(fields["summary"]["table_count"], 0)

    def test_helper_output_is_json_serializable(self):
        json.dumps(server.build_docx_structured_fields({
            "attachments": ["附件"],
            "tables": [{"headers": ["A"], "rows": [["B"]]}],
        }), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
