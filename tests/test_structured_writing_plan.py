"""Stage 3 structured writing plan v1 tests.

The plan is deterministic linkage metadata only. It does not call a model,
query the evidence library, or perform semantic entailment.
"""

import importlib.util
import json
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _fields(genre="work_plan"):
    return {name: "filled" for name in server.RULES["genres"][genre]["required_fields"]}


class StructuredWritingPlanTest(unittest.TestCase):
    def test_analyze_payload_always_includes_structured_writing_plan(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        self.assertIn("structured_writing_plan", res)
        plan = res["structured_writing_plan"]
        self.assertEqual(plan["method"], "structured_writing_plan_v1")
        self.assertIn("outline", plan)
        self.assertIn("paragraphs", plan)
        self.assertIn("summary", plan)

    def test_no_draft_has_empty_paragraphs_and_missing_outline(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "meeting notes",
        })
        plan = res["structured_writing_plan"]
        self.assertEqual(plan["paragraphs"], [])
        self.assertTrue(plan["outline"])
        self.assertTrue(all(not entry["present"] for entry in plan["outline"]))
        self.assertEqual(plan["summary"]["paragraph_count"], 0)
        self.assertEqual(plan["summary"]["missing_section_count"], len(plan["outline"]))

    def test_paragraph_without_required_markers_is_no_claim_markers(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "meeting notes",
            "draft": "Plain coordination text without numeric or policy markers.",
        })
        paragraph = res["structured_writing_plan"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "no_claim_markers")
        self.assertEqual(paragraph["required_markers"], [])
        self.assertEqual(paragraph["linked_chunk_ids"], [])

    def test_payload_evidence_exactly_supports_paragraph_marker(self):
        draft = "By 2026, complete 15 service counters."
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "meeting notes",
            "draft": draft,
            "evidence": [{"title": "Work ledger", "body": "By 2026, complete 15 service counters."}],
        })
        paragraph = res["structured_writing_plan"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "supported")
        self.assertIn("payload-evidence-1", paragraph["linked_chunk_ids"])
        self.assertEqual(paragraph["evidence_map"]["missing_markers"], [])

    def test_uncovered_marker_needs_verification(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "meeting notes",
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [{"title": "Work ledger", "body": "By 2026, complete service counters."}],
        })
        paragraph = res["structured_writing_plan"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "needs_verification")
        self.assertIn("15", paragraph["evidence_map"]["missing_markers"])
        self.assertIn("missing_required_markers", paragraph["warnings"])

    def test_missing_section_count_is_deterministic(self):
        section = server.RULES["genres"]["work_plan"]["required_sections"][0]
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "meeting notes",
            "draft": f"{section}\nPlain coordination text.",
        })
        plan = res["structured_writing_plan"]
        self.assertTrue(plan["outline"][0]["present"])
        self.assertEqual(plan["outline"][0]["paragraph_indexes"], [1])
        self.assertEqual(plan["summary"]["missing_section_count"], len(plan["outline"]) - 1)

    def test_structured_writing_plan_is_json_serializable(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "meeting notes",
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [{"title": "Work ledger", "body": "By 2026, complete 15 service counters."}],
        })
        json.dumps(res["structured_writing_plan"])


if __name__ == "__main__":
    unittest.main()
