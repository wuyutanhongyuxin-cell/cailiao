"""Stage 5 real anonymized query-set intake scaffold tests.

Honesty guard: these tests must NOT contain a real query set. They build minimal
synthetic cases in memory to exercise the scaffold; none of them can (or should)
classify as a completed real set beyond shape/readiness mechanics.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
TEMPLATE = ROOT / "tests" / "data" / "real_query_set_template.json"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _clean_case(i):
    """A structurally valid, anonymized, provenance-bearing case (no markers)."""
    return {
        "id": f"q{i:03d}",
        "query": f"某类公文的检索查询 {i}",
        "provenance": {"source": "intake", "collected_at": "2026-07-01T00:00:00", "anonymized": True},
        "relevant_titles": [f"relevant-{i}"],
    }


def _real_like_set(n, name="anon intake set"):
    return {"metadata": {"name": name, "version": "v1"}, "cases": [_clean_case(i) for i in range(n)]}


class TemplateReadinessTest(unittest.TestCase):
    def test_template_validates_structurally_but_is_not_ready(self):
        dataset = server.load_real_query_set(TEMPLATE)
        report = server.summarize_real_query_readiness(dataset)
        # Template is a valid shape...
        self.assertTrue(report["validation"]["passed"], report["validation"]["errors"])
        # ...but must classify as template, never ready_real.
        self.assertEqual(report["status"], "template")
        self.assertFalse(report["ready"])

    def test_template_has_placeholder_marker(self):
        dataset = server.load_real_query_set(TEMPLATE)
        self.assertTrue(server.validate_real_query_set(dataset)["has_placeholder_marker"])


class ReadinessThresholdTest(unittest.TestCase):
    def test_fifty_clean_cases_are_ready(self):
        report = server.summarize_real_query_readiness(_real_like_set(50))
        self.assertEqual(report["status"], "ready_real")
        self.assertTrue(report["ready"])
        self.assertEqual(report["case_count"], 50)

    def test_under_fifty_is_incomplete(self):
        report = server.summarize_real_query_readiness(_real_like_set(49))
        self.assertEqual(report["status"], "incomplete_real")
        self.assertFalse(report["ready"])

    def test_over_hundred_is_oversized(self):
        report = server.summarize_real_query_readiness(_real_like_set(101))
        self.assertEqual(report["status"], "oversized_real")
        self.assertFalse(report["ready"])

    def test_synthetic_marker_blocks_ready_even_with_enough_cases(self):
        dataset = _real_like_set(60, name="synthetic benchmark set")  # marker in metadata
        report = server.summarize_real_query_readiness(dataset)
        self.assertEqual(report["status"], "template")
        self.assertFalse(report["ready"])

    def test_placeholder_token_in_case_blocks_ready(self):
        dataset = _real_like_set(60)
        dataset["cases"][0]["query"] = "这是一个 placeholder 查询"
        report = server.summarize_real_query_readiness(dataset)
        self.assertFalse(report["ready"])
        self.assertEqual(report["status"], "template")


class AnonymizationTest(unittest.TestCase):
    def test_forbidden_pii_field_rejected(self):
        dataset = _real_like_set(50)
        dataset["cases"][0]["email"] = "someone@example.com"
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])
        self.assertTrue(any("PII/secret field" in e for e in report["errors"]))

    def test_pii_shaped_value_rejected_without_leaking_value(self):
        dataset = _real_like_set(50)
        dataset["cases"][0]["query"] = "请联系 13800138000 办理"
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])
        pii_errors = [e for e in report["errors"] if "PII-shaped" in e]
        self.assertTrue(pii_errors)
        # The raw phone number must never appear in the error text.
        self.assertFalse(any("13800138000" in e for e in report["errors"]))

    def test_id_card_shaped_value_rejected(self):
        dataset = _real_like_set(50)
        dataset["cases"][1]["query"] = "身份证 11010119900307391X 的申请"
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])
        self.assertFalse(any("11010119900307391X" in e for e in report["errors"]))

    def test_missing_provenance_fails(self):
        dataset = _real_like_set(50)
        dataset["cases"][0].pop("provenance")
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])
        self.assertTrue(any("provenance" in e for e in report["errors"]))

    def test_not_anonymized_flag_fails(self):
        dataset = _real_like_set(50)
        dataset["cases"][0]["provenance"]["anonymized"] = False
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])

    def test_missing_relevance_target_fails(self):
        dataset = _real_like_set(50)
        dataset["cases"][0].pop("relevant_titles")
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])

    def test_duplicate_id_fails(self):
        dataset = _real_like_set(50)
        dataset["cases"][1]["id"] = dataset["cases"][0]["id"]
        report = server.validate_real_query_set(dataset)
        self.assertFalse(report["passed"])


class LoadAndCliTest(unittest.TestCase):
    def test_load_missing_cases_raises(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"metadata": {}}, f)
            path = f.name
        with self.assertRaises(ValueError):
            server.load_real_query_set(path)

    def test_cli_on_template_exits_nonzero(self):
        spec_cli = importlib.util.spec_from_file_location(
            "validate_real_query_set", ROOT / "tools" / "validate_real_query_set.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--set", str(TEMPLATE), "--json"])
        # Template is not a completed real set -> exit 1 (not ready).
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
