"""Stage 4 deterministic DOCX style profile tests."""

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


class DocxStyleProfileTest(unittest.TestCase):
    def test_default_profile_has_method_and_boundary_metadata(self):
        profile = server.build_docx_style_profile()
        self.assertEqual(profile["method"], "docx_style_profile_v1")
        self.assertEqual(profile["standard"], "GB/T 9704-2012-inspired")
        self.assertIn("not full formal layout certification", profile["boundary"])
        self.assertEqual(profile["page"]["size"], "A4")
        self.assertEqual(profile["style_ids"]["title"], "MaterialTitle")

    def test_override_normalization_for_margins_font_and_line_spacing(self):
        profile = server.build_docx_style_profile({
            "style_profile": {
                "margins_twips": {"top": "1800", "left": -100, "right": 99999},
                "font_family": "KaiTi",
                "line_spacing_twips": "600",
                "body_font_size_half_points": "30",
            },
        })
        self.assertEqual(profile["page"]["margins_twips"]["top"], 1800)
        self.assertEqual(profile["page"]["margins_twips"]["left"], 0)
        self.assertEqual(profile["page"]["margins_twips"]["right"], 4320)
        self.assertEqual(profile["fonts"]["body"], "KaiTi")
        self.assertEqual(profile["paragraph"]["line_spacing_twips"], 600)
        self.assertEqual(profile["font_size_half_points"]["body"], 30)

    def test_export_docx_backwards_compatible_and_valid_zip(self):
        raw = server.export_docx("Title", "Body paragraph.")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
        self.assertIn("word/document.xml", names)
        self.assertIn("[Content_Types].xml", names)

    def test_generated_docx_contains_styles_part_and_relationship(self):
        raw = server.export_docx("Title", "Body paragraph.")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            styles = z.read("word/styles.xml").decode("utf-8")
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
            content_types = z.read("[Content_Types].xml").decode("utf-8")
        self.assertIn("word/styles.xml", names)
        self.assertIn("Material Body", styles)
        self.assertIn("relationships/styles", rels)
        self.assertIn("wordprocessingml.styles+xml", content_types)

    def test_document_references_title_and_body_style_ids(self):
        raw = server.export_docx("Title", "First body.\n\nSecond body.")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            document = z.read("word/document.xml").decode("utf-8")
        self.assertIn('<w:pStyle w:val="MaterialTitle"/>', document)
        self.assertEqual(document.count('<w:pStyle w:val="MaterialBody"/>'), 2)

    def test_profile_is_json_serializable(self):
        json.dumps(server.build_docx_style_profile({
            "margins_twips": {"top": 1800},
            "font_family": "FangSong",
        }))


if __name__ == "__main__":
    unittest.main()
