"""Stage 3 deterministic approved-facts audit tests."""

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


class ApprovedFactsAuditTest(unittest.TestCase):
    def test_analyze_payload_always_includes_approved_facts_audit(self):
        res = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        self.assertIn("approved_facts_audit", res)
        audit = res["approved_facts_audit"]
        self.assertEqual(audit["method"], "approved_facts_audit_v1")
        self.assertIn("paragraphs", audit)
        self.assertIn("summary", audit)

    def test_no_marker_paragraph_is_no_claim_markers(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed coordination facts",
            "draft": "Plain coordination paragraph.",
        })
        paragraph = res["approved_facts_audit"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "no_claim_markers")
        self.assertEqual(paragraph["required_markers"], [])
        self.assertEqual(paragraph["approved_fact_ids"], [])

    def test_marker_covered_by_payload_facts_is_approved(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "By 2026, complete 15 service counters.",
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [],
        })
        audit = res["approved_facts_audit"]
        paragraph = audit["paragraphs"][0]
        self.assertEqual(paragraph["status"], "all_facts_approved")
        self.assertEqual(paragraph["unapproved_markers"], [])
        self.assertIn("payload-facts", paragraph["approved_fact_ids"])
        self.assertEqual(audit["source_counts"]["facts"], 1)

    def test_marker_covered_by_structured_approved_facts_is_approved(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "",
            "approved_facts": [{"id": "ledger", "text": "By 2026, complete 15 service counters."}],
            "draft": "By 2026, complete 15 service counters.",
        })
        paragraph = res["approved_facts_audit"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "all_facts_approved")
        self.assertIn("payload-approved-fact-ledger", paragraph["approved_fact_ids"])

    def test_marker_covered_by_explicitly_approved_evidence_is_approved(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "",
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [{"title": "ledger", "body": "By 2026, complete 15 service counters.",
                          "approved": True}],
        })
        paragraph = res["approved_facts_audit"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "all_facts_approved")
        self.assertIn("payload-evidence-1", paragraph["approved_fact_ids"])

    def test_unapproved_marker_is_flagged(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "By 2026, complete service counters.",
            "draft": "By 2026, complete 15 service counters.",
            "evidence": [{"title": "ledger", "body": "By 2026, complete 15 service counters."}],
        })
        paragraph = res["approved_facts_audit"]["paragraphs"][0]
        self.assertEqual(paragraph["status"], "uses_unapproved_facts")
        self.assertIn("15", paragraph["unapproved_markers"])
        self.assertIn("unapproved_or_missing_markers", paragraph["warnings"])

    def test_payload_is_not_mutated_and_output_is_json_serializable(self):
        payload = {
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "By 2026, complete 15 service counters.",
            "draft": "By 2026, complete 15 service counters.",
            "approved_facts": [{"id": "ledger", "text": "By 2026, complete 15 service counters."}],
        }
        snapshot = json.loads(json.dumps(payload))
        res = server.analyze_payload(payload)
        self.assertEqual(payload, snapshot)
        json.dumps(res["approved_facts_audit"])


if __name__ == "__main__":
    unittest.main()
