"""Stage 3 deterministic unit-template and forbidden expression tests."""

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


class UnitTemplateForbiddenTest(unittest.TestCase):
    def test_no_template_returns_disabled_empty_profile(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
        })
        profile = res["unit_template_profile"]
        self.assertEqual(profile["method"], "unit_template_profile_v1")
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["preferred_terms"], [])
        self.assertEqual(profile["forbidden_terms"], [])
        self.assertEqual(profile["summary"]["forbidden_term_count"], 0)

    def test_template_normalizes_terms_and_counts(self):
        profile = server.build_unit_template_profile({
            "unit_template": {
                "unit_name": "Research Office",
                "preferred_terms": ["service ledger", "service ledger", "", None],
                "forbidden_terms": ["great success", "great success", "  "],
                "required_signature": "Research Office",
                "contact": "ops@example.test",
                "style_notes": "Use concise wording.",
            },
        }, server.RULES["genres"]["work_plan"])
        self.assertTrue(profile["enabled"])
        self.assertEqual(profile["preferred_terms"], ["service ledger"])
        self.assertEqual(profile["forbidden_terms"], ["great success"])
        self.assertEqual(profile["summary"]["preferred_term_count"], 1)
        self.assertTrue(profile["summary"]["has_required_signature"])

    def test_prompt_includes_configured_template_section(self):
        payload = {
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
            "unit_template": {
                "unit_name": "Research Office",
                "preferred_terms": ["service ledger"],
                "forbidden_terms": ["great success"],
                "required_signature": "Research Office",
            },
        }
        analysis = server.analyze_payload(payload)
        prompt = server.build_prompt(payload, analysis)
        self.assertIn("Unit template constraints:", prompt)
        self.assertIn("Research Office", prompt)
        self.assertIn("service ledger", prompt)
        self.assertIn("great success", prompt)

    def test_forbidden_audit_merges_sources(self):
        payload = {
            "draft": "This draft says great success and never use this.",
            "unit_template": {"forbidden_terms": ["great success"]},
            "forbidden_phrases": ["never use this"],
        }
        audit = server.build_forbidden_expression_audit(payload)
        matches = audit["paragraphs"][0]["matches"]
        explicit = {(m["phrase"], m["source"], m["severity"]) for m in matches}
        self.assertIn(("great success", "unit_template", "fail"), explicit)
        self.assertIn(("never use this", "payload", "fail"), explicit)
        self.assertEqual(audit["summary"]["paragraphs_with_matches"], 1)

    def test_explicit_forbidden_term_creates_p_target_fail_issue(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
            "draft": "Use banned slogan here.",
            "unit_template": {"forbidden_terms": ["banned slogan"]},
        })
        issues = [i for i in res["issues"] if i["code"] == "forbidden_expression"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["level"], "fail")
        self.assertEqual(issues[0]["target"], "p1")
        self.assertEqual(issues[0]["phrase"], "banned slogan")
        self.assertEqual(issues[0]["source"], "unit_template")

    def test_duplicate_phrase_does_not_create_duplicate_issue_spam(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
            "draft": "Do not say banned slogan. The banned slogan appears twice.",
            "unit_template": {"forbidden_terms": ["banned slogan", "banned slogan"]},
            "forbidden_phrases": ["banned slogan"],
        })
        issues = [i for i in res["issues"] if i["code"] == "forbidden_expression"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["target"], "p1")

    def test_json_serializable_and_existing_keys_remain(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
            "draft": "Clean paragraph.",
            "unit_template": {"unit_name": "Research Office"},
        })
        for key in ("status", "score", "issues", "missing", "genre",
                    "structured_writing_plan", "writing_state", "approved_facts_audit",
                    "targeted_repair_plan", "draft_version"):
            self.assertIn(key, res)
        json.dumps(res["unit_template_profile"])
        json.dumps(res["forbidden_expression_audit"])


if __name__ == "__main__":
    unittest.main()
