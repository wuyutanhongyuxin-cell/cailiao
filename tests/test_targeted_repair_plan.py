"""Stage 3 deterministic targeted paragraph repair plan tests."""

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


class TargetedRepairPlanTest(unittest.TestCase):
    def test_analyze_payload_always_includes_targeted_repair_plan(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        self.assertIn("targeted_repair_plan", res)
        plan = res["targeted_repair_plan"]
        self.assertEqual(plan["method"], "targeted_repair_plan_v1")
        self.assertIn("repair_units", plan)
        self.assertIn("summary", plan)

    def test_no_draft_has_no_repair_units(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
        })
        plan = res["targeted_repair_plan"]
        self.assertFalse(plan["can_repair"])
        self.assertEqual(plan["repair_units"], [])
        self.assertEqual(plan["summary"]["paragraph_count"], 0)

    def test_blocker_prevents_repair(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": {},
            "facts": "",
            "draft": "By 2026, complete 15 service counters.",
        })
        plan = res["targeted_repair_plan"]
        self.assertFalse(plan["can_repair"])
        self.assertEqual(plan["repair_units"], [])
        self.assertTrue(plan["summary"]["blocked"])

    def test_clean_draft_has_no_repair_units(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "Office confirms coordination work.",
            "draft": "Office coordinates the work and records progress.",
        })
        plan = res["targeted_repair_plan"]
        self.assertFalse(plan["can_repair"])
        self.assertEqual(plan["repair_units"], [])

    def test_issue_target_p1_creates_only_one_paragraph_unit(self):
        draft = (
            "要加强组织领导，形成工作合力，确保取得实效。\n\n"
            "Office coordinates the work and records progress."
        )
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
            "draft": draft,
        })
        plan = res["targeted_repair_plan"]
        self.assertTrue(plan["can_repair"])
        self.assertEqual([u["paragraph_index"] for u in plan["repair_units"]], [1])
        unit = plan["repair_units"][0]
        self.assertIn("vague_without_guard", unit["issue_codes"])
        self.assertEqual(unit["source_targets"], ["p1"])
        self.assertEqual(unit["scope"], "paragraph_only")
        self.assertFalse(unit["locked"])
        self.assertIn("Rewrite only paragraph 1", unit["instruction"])
        self.assertIn("Preserve all unrelated paragraphs", unit["instruction"])

    def test_structured_missing_marker_creates_repair_unit(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "By 2026, complete service counters.",
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [{"title": "ledger", "body": "By 2026, complete service counters."}],
        })
        unit = res["targeted_repair_plan"]["repair_units"][0]
        self.assertEqual(unit["paragraph_index"], 1)
        self.assertIn("15", unit["missing_markers"])
        self.assertIn("15", unit["required_markers"])

    def test_unapproved_fact_marker_creates_repair_unit(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "By 2026, complete service counters.",
            "approved_facts": [{"id": "year", "text": "By 2026, complete service counters."}],
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [{"title": "ledger", "body": "By 2026, complete 15 service counters."}],
        })
        unit = res["targeted_repair_plan"]["repair_units"][0]
        self.assertEqual(unit["paragraph_index"], 1)
        self.assertIn("15", unit["unapproved_markers"])
        self.assertIn("payload-approved-fact-year", unit["allowed_fact_ids"])
        self.assertIn("allowed_fact_ids", unit["instruction"])

    def test_targeted_repair_plan_is_json_serializable_and_additive(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "By 2026, complete service counters.",
            "draft": "By 2026, complete 15 service counters.",
        })
        for key in ("status", "score", "issues", "missing", "genre",
                    "structured_writing_plan", "writing_state", "approved_facts_audit"):
            self.assertIn(key, res)
        json.dumps(res["targeted_repair_plan"])


if __name__ == "__main__":
    unittest.main()
