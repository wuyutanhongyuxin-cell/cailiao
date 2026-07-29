"""Stage 3 deterministic draft version, lock, diff, and rollback tests."""

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


class DraftVersionTest(unittest.TestCase):
    def test_build_draft_version_paragraphs_and_locks(self):
        version = server.build_draft_version({
            "draft": "First paragraph.\n\nSecond paragraph.",
            "locked_paragraphs": [2, "bad", 9],
        })
        self.assertEqual(version["method"], "draft_version_v1")
        self.assertEqual(version["paragraph_count"], 2)
        self.assertEqual(version["locked_paragraphs"], [2])
        self.assertFalse(version["paragraphs"][0]["locked"])
        self.assertTrue(version["paragraphs"][1]["locked"])

    def test_analyze_payload_includes_draft_version(self):
        res = server.analyze_payload({
            "genre": "work_plan",
            "fields": _fields(),
            "facts": "confirmed facts",
            "draft": "First paragraph.",
            "locked_paragraphs": [1],
        })
        self.assertIn("draft_version", res)
        self.assertEqual(res["draft_version"]["locked_paragraphs"], [1])

    def test_diff_reports_changed_added_removed_unchanged(self):
        previous = server.build_draft_version({"draft": "A\n\nB\n\nC"}, version_id="v1")
        current = {
            "version_id": "v2",
            "paragraphs": [
                {"index": 1, "text": "A", "locked": False},
                {"index": 2, "text": "B changed", "locked": False},
                {"index": 4, "text": "D", "locked": False},
            ],
        }
        diff = server.diff_draft_versions(previous, current)
        by_index = {entry["index"]: entry["status"] for entry in diff["entries"]}
        self.assertEqual(by_index, {1: "unchanged", 2: "changed", 3: "removed", 4: "added"})
        self.assertEqual(diff["summary"]["changed_count"], 1)
        self.assertEqual(diff["summary"]["added_count"], 1)
        self.assertEqual(diff["summary"]["removed_count"], 1)
        self.assertEqual(diff["summary"]["unchanged_count"], 1)

    def test_apply_revisions_skips_locked_and_applies_unlocked(self):
        base = server.build_draft_version({
            "draft": "Locked paragraph.\n\nOpen paragraph.",
            "locked_paragraphs": [1],
        }, version_id="base")
        result = server.apply_paragraph_revisions(base, [
            {"paragraph_index": 1, "text": "Should not apply."},
            {"paragraph_index": 2, "text": "Updated open paragraph."},
        ])
        new_version = result["version"]
        self.assertEqual(new_version["paragraphs"][0]["text"], "Locked paragraph.")
        self.assertEqual(new_version["paragraphs"][1]["text"], "Updated open paragraph.")
        self.assertEqual(result["applied_revisions"], [{"paragraph_index": 2}])
        self.assertEqual(result["skipped_locked"][0]["paragraph_index"], 1)

    def test_apply_revisions_tracks_invalid_and_does_not_mutate_base(self):
        base = server.build_draft_version({"draft": "A\n\nB"}, version_id="base")
        snapshot = json.loads(json.dumps(base))
        result = server.apply_paragraph_revisions(base, {
            "2": "B2",
            "3": "missing",
            "bad": "bad",
        }, locked_indexes=[2])
        self.assertEqual(base, snapshot)
        self.assertEqual(result["version"]["paragraphs"][1]["text"], "B")
        reasons = {item["reason"] for item in result["invalid_revisions"]}
        self.assertIn("paragraph_not_found", reasons)
        self.assertIn("invalid_paragraph_index", reasons)
        self.assertEqual(result["skipped_locked"][0]["paragraph_index"], 2)

    def test_rollback_reconstructs_draft(self):
        version = server.build_draft_version({"draft": "A\n\nB"}, version_id="v1")
        rollback = server.rollback_draft_version(version)
        self.assertEqual(rollback["method"], "draft_version_rollback_v1")
        self.assertEqual(rollback["restored_version_id"], "v1")
        self.assertEqual(rollback["draft"], "A\n\nB")
        self.assertEqual(rollback["paragraph_count"], 2)

    def test_outputs_are_json_serializable(self):
        base = server.build_draft_version({"draft": "A\n\nB", "locked_paragraphs": [1]})
        revised = server.apply_paragraph_revisions(base, [{"paragraph_index": 2, "text": "B2"}])
        diff = server.diff_draft_versions(base, revised["version"])
        rollback = server.rollback_draft_version(base)
        json.dumps({"base": base, "revised": revised, "diff": diff, "rollback": rollback})


if __name__ == "__main__":
    unittest.main()
