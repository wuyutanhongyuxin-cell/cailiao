"""Stage 2B public DoIT real-query intake tool tests.

Honesty guard: these tests use an INVENTED fixture (tests/data/doit_fixture.sample.jsonl)
that is explicitly fixture data, not real DoIT evidence and not private telemetry. They
never download anything, never read secrets, and never check a ROADMAP parent item.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "prepare_stage2b_real_query_set.py"
FIXTURE = ROOT / "tests" / "data" / "doit_fixture.sample.jsonl"

spec = importlib.util.spec_from_file_location("prepare_stage2b_real_query_set", TOOL)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def _records():
    return list(prep._iter_records(FIXTURE))


class SelectionTest(unittest.TestCase):
    def test_selects_only_valid_unique_creative_writing_chinese_prompts(self):
        sel = prep.select_creative_writing_prompts(_records(), min_cases=50, max_cases=100)
        cases = sel["cases"]
        self.assertGreaterEqual(len(cases), 50)
        self.assertLessEqual(len(cases), 100)
        # Every selected prompt is a valid Chinese prompt.
        for c in cases:
            self.assertTrue(prep._is_valid_prompt(c["query"]))
        # Unique by text.
        self.assertEqual(len({c["query"] for c in cases}), len(cases))
        # Noise was filtered: at least one rejected (short/empty/non-Chinese) and one duplicate.
        self.assertGreaterEqual(sel["rejected"], 1)
        self.assertGreaterEqual(sel["duplicates"], 1)

    def test_selection_is_deterministic(self):
        a = prep.select_creative_writing_prompts(_records())["cases"]
        b = prep.select_creative_writing_prompts(_records())["cases"]
        self.assertEqual([c["query"] for c in a], [c["query"] for c in b])

    def test_max_cap_respected(self):
        sel = prep.select_creative_writing_prompts(_records(), min_cases=10, max_cases=20)
        self.assertEqual(len(sel["cases"]), 20)


class ArtifactTest(unittest.TestCase):
    def test_build_artifact_shape_and_no_answers(self):
        art = prep.build_artifact(_records())
        self.assertEqual(art["method"], "stage2b_real_query_candidate_set_v1")
        self.assertEqual(art["source"]["url"], prep.DOIT_SOURCE_URL)
        self.assertEqual(art["source"]["license"], "MIT")
        self.assertFalse(art["contains_assistant_answers"])
        self.assertFalse(art["roadmap_parent_items_checked"])
        self.assertEqual(art["record_count"], len(art["cases"]))
        # No assistant/answer field anywhere in the serialized artifact.
        blob = json.dumps(art, ensure_ascii=False)
        for banned in ('"answer"', '"assistant"', '"response"', '"output"', '"completion"'):
            self.assertNotIn(banned, blob)

    def test_hashes_present_and_correct(self):
        art = prep.build_artifact(_records())
        for c in art["cases"]:
            self.assertEqual(c["query_sha256"], prep._sha256(prep._norm(c["query"])))

    def test_build_artifact_validates(self):
        art = prep.build_artifact(_records())
        report = prep.validate_artifact(art)
        self.assertTrue(report["passed"], report["errors"])


class ValidationFailureTest(unittest.TestCase):
    def _art(self):
        return prep.build_artifact(_records())

    def test_wrong_license_fails(self):
        art = self._art()
        art["source"]["license"] = "Apache-2.0"
        self.assertFalse(prep.validate_artifact(art)["passed"])

    def test_wrong_source_url_fails(self):
        art = self._art()
        art["source"]["url"] = "https://example.com/not-doit"
        self.assertFalse(prep.validate_artifact(art)["passed"])

    def test_count_out_of_band_fails(self):
        art = self._art()
        art["cases"] = art["cases"][:10]
        art["record_count"] = 10
        self.assertFalse(prep.validate_artifact(art)["passed"])

    def test_tampered_hash_fails(self):
        art = self._art()
        art["cases"][0]["query_sha256"] = "sha256:deadbeef"
        self.assertFalse(prep.validate_artifact(art)["passed"])

    def test_tampered_set_hash_fails(self):
        # Tamper ONLY the set_hash (all per-case hashes remain correct); validation
        # must fail because set_hash no longer matches the ordered per-case hashes.
        art = self._art()
        art["set_hash"] = "sha256:" + ("0" * 64)
        report = prep.validate_artifact(art)
        self.assertFalse(report["passed"])
        self.assertTrue(any("set_hash" in e for e in report["errors"]))

    def test_missing_set_hash_fails(self):
        art = self._art()
        del art["set_hash"]
        report = prep.validate_artifact(art)
        self.assertFalse(report["passed"])
        self.assertTrue(any("set_hash" in e for e in report["errors"]))

    def test_embedded_answer_fails(self):
        art = self._art()
        art["cases"][0]["answer"] = "leaked assistant answer"
        report = prep.validate_artifact(art)
        self.assertFalse(report["passed"])
        self.assertTrue(any("assistant-answer field" in e for e in report["errors"]))

    def test_record_count_mismatch_fails(self):
        art = self._art()
        art["record_count"] = art["record_count"] + 1
        self.assertFalse(prep.validate_artifact(art)["passed"])

    def test_non_object_fails_safely(self):
        self.assertFalse(prep.validate_artifact(["nope"])["passed"])


class CliTest(unittest.TestCase):
    def test_prepare_then_validate_roundtrip(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            out = f.name
        rc = prep.main(["prepare", "--input", str(FIXTURE), "--output", out])
        self.assertEqual(rc, 0)
        self.assertEqual(prep.main(["validate", "--artifact", out]), 0)

    def test_prepare_insufficient_min_exits_one(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            out = f.name
        # Require more than the fixture can supply -> exit 1, no fabrication.
        rc = prep.main(["prepare", "--input", str(FIXTURE), "--output", out, "--min", "500"])
        self.assertEqual(rc, 1)

    def test_missing_input_exits_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            out = f.name
        rc = prep.main(["prepare", "--input", "/no/such/path.jsonl", "--output", out])
        self.assertEqual(rc, 2)


class FixtureMarkerTest(unittest.TestCase):
    def test_fixture_is_marked_not_real_evidence(self):
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("FIXTURE ONLY", text)


if __name__ == "__main__":
    unittest.main()
