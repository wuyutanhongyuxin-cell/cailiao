"""Stage 2B DoIT -> ready_real BM25 dataset builder tests.

Honesty guard: uses an INVENTED fixture (tests/data/doit_bm25_fixture.sample.jsonl,
first record marked 'FIXTURE ONLY … not real DoIT evidence'). Never downloads, never
reads secrets, never checks a ROADMAP parent. The public DoIT answers here are invented
fixtures; the tool treats real DoIT answers as public MIT benchmark data, not telemetry.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "build_stage2b_doit_bm25_dataset.py"
SERVER = ROOT / "backend" / "server.py"
FIXTURE = ROOT / "tests" / "data" / "doit_bm25_fixture.sample.jsonl"

spec = importlib.util.spec_from_file_location("build_stage2b_doit_bm25_dataset", TOOL)
bld = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bld)

sspec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(sspec)
sspec.loader.exec_module(server)


def _records():
    return list(bld._intake._iter_records(FIXTURE))


def _dataset():
    return bld.build_dataset(_records(), source_file="curated/1000/creative_writing_1000.json")


class BuildTest(unittest.TestCase):
    def test_snake_case_and_display_labels_both_accepted(self):
        ds = _dataset()
        raw = {c["source_type_raw"] for c in ds["cases"]}
        self.assertIn("creative_writing", raw)   # actual real DoIT file label
        self.assertIn("Creative Writing", raw)    # dataset-card display label

    def test_built_dataset_is_ready_real(self):
        ds = _dataset()
        r = server.summarize_real_query_readiness(ds)
        self.assertEqual(r["status"], "ready_real")
        self.assertTrue(r["ready"])
        self.assertGreaterEqual(ds["record_count"], 50)
        self.assertLessEqual(ds["record_count"], 100)

    def test_every_case_has_paired_corpus_target(self):
        ds = _dataset()
        titles = {d["title"] for d in ds["corpus"]}
        self.assertTrue(ds["corpus"])
        self.assertEqual(len(ds["corpus"]), ds["record_count"])
        for c in ds["cases"]:
            self.assertTrue(c["relevant_titles"])
            for t in c["relevant_titles"]:
                self.assertIn(t, titles)

    def test_cases_carry_no_assistant_answers(self):
        ds = _dataset()
        for c in ds["cases"]:
            for banned in ("answer", "assistant", "response", "output", "completion"):
                self.assertNotIn(banned, c)
        self.assertFalse(ds["contains_assistant_answers_in_cases"])

    def test_metadata_license_and_source(self):
        ds = _dataset()
        self.assertEqual(ds["metadata"]["license"], "MIT")
        self.assertEqual(ds["metadata"]["source_url"], bld.DOIT_SOURCE_URL)
        self.assertEqual(ds["metadata"]["source_file"], "curated/1000/creative_writing_1000.json")
        self.assertFalse(ds["metadata"]["is_template"])
        self.assertFalse(ds["roadmap_parent_items_checked"])

    def test_deterministic(self):
        a = _dataset()
        b = _dataset()
        self.assertEqual(a["set_hash"], b["set_hash"])
        self.assertEqual([c["query"] for c in a["cases"]], [c["query"] for c in b["cases"]])

    def test_json_serializable(self):
        json.dumps(_dataset(), ensure_ascii=False)


class ValidateTest(unittest.TestCase):
    def test_valid_dataset_passes_and_ready_for_bm25(self):
        rep = bld.validate_dataset(_dataset(), server=server)
        self.assertTrue(rep["passed"], rep["errors"])
        self.assertEqual(rep["status"], "ready_real")
        self.assertTrue(rep["ready_for_bm25"])

    def test_missing_corpus_fails(self):
        ds = _dataset()
        ds["corpus"] = []
        self.assertFalse(bld.validate_dataset(ds, server=server)["passed"])

    def test_tampered_set_hash_fails(self):
        ds = _dataset()
        ds["set_hash"] = "sha256:" + ("0" * 64)
        rep = bld.validate_dataset(ds, server=server)
        self.assertFalse(rep["passed"])
        self.assertTrue(any("set_hash" in e for e in rep["errors"]))

    def test_wrong_license_fails(self):
        ds = _dataset()
        ds["metadata"]["license"] = "Apache-2.0"
        self.assertFalse(bld.validate_dataset(ds, server=server)["passed"])

    def test_relevance_target_not_in_corpus_fails(self):
        ds = _dataset()
        ds["cases"][0]["relevant_titles"] = ["no-such-doc"]
        self.assertFalse(bld.validate_dataset(ds, server=server)["passed"])


class Bm25SweepTest(unittest.TestCase):
    def test_gated_sweep_runs_on_built_dataset(self):
        ds = _dataset()
        # Reduced grid keeps this fast; still exercises the real gate + eval harness.
        sweep = server.run_bm25_sweep_on_real_query_set(ds, {"k1": [1.2], "b": [0.75], "thresholds": [0.0]})
        self.assertTrue(sweep["ran"])
        self.assertFalse(sweep["refused"])
        self.assertIn("best", sweep)
        self.assertIn("title_recall_at_k", sweep["best"])


class InsufficientTest(unittest.TestCase):
    def test_too_few_records_does_not_fabricate(self):
        # Only 3 CW prompt-answer records -> cannot reach min 50; build must not fabricate.
        recs = [{"idx": i, "type": "creative_writing", "question_format": "open_ended",
                 "messages": [{"role": "user", "content": f"写一段关于主题{i}的中文短文，细节具体。"},
                              {"role": "assistant", "content": f"关于主题{i}的中文短文答案，具体而真挚。"}]} for i in range(3)]
        ds = bld.build_dataset(recs)
        self.assertEqual(ds["record_count"], 3)
        self.assertEqual(server.summarize_real_query_readiness(ds)["status"], "incomplete_real")

    def test_cli_build_insufficient_min_exits_one(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            out = f.name
        rc = bld.main(["build", "--input", str(FIXTURE), "--output", out, "--min", "500"])
        self.assertEqual(rc, 1)

    def test_cli_build_then_validate_roundtrip(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            out = f.name
        self.assertEqual(bld.main(["build", "--input", str(FIXTURE), "--output", out]), 0)
        self.assertEqual(bld.main(["validate", "--set", out]), 0)

    def test_missing_input_exits_two(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            out = f.name
        self.assertEqual(bld.main(["build", "--input", "/no/such/dir", "--output", out]), 2)


class FixtureMarkerTest(unittest.TestCase):
    def test_fixture_marked_not_real_evidence(self):
        self.assertIn("FIXTURE ONLY", FIXTURE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
