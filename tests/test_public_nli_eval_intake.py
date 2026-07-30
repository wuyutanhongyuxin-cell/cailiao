"""Public NLI semantic eval intake tests.

These tests use a tiny fixture only. They do not download a dataset and do not
call a real NLI/LLM provider.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
FIXTURE = ROOT / "tests" / "data" / "public_nli_fixture.sample.jsonl"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class PublicNliEvalIntakeTest(unittest.TestCase):
    def _dataset(self):
        records = server.load_public_nli_eval_records(FIXTURE)
        return server.build_public_nli_semantic_eval_dataset(records, {
            "dataset_type": "snli",
            "source_url": "https://nlp.stanford.edu/projects/snli/",
            "license": "fixture-only",
        })

    def test_builds_valid_dataset_from_local_jsonl(self):
        dataset = self._dataset()
        self.assertEqual(dataset["record_count"], 3)
        self.assertFalse(dataset["is_real_nli_provider_evidence"])
        self.assertFalse(dataset["roadmap_parent_items_checked"])
        self.assertEqual({c["expected_verdict"] for c in dataset["cases"]},
                         {"entailment", "contradiction", "neutral"})
        result = server.validate_public_nli_semantic_eval_dataset(dataset)
        self.assertTrue(result["passed"], result)
        json.dumps(dataset, ensure_ascii=False)

    def test_template_and_provider_evidence_claims_are_rejected(self):
        dataset = self._dataset()
        dataset["is_template"] = True
        self.assertFalse(server.validate_public_nli_semantic_eval_dataset(dataset)["passed"])
        dataset = self._dataset()
        dataset["is_real_nli_provider_evidence"] = True
        self.assertFalse(server.validate_public_nli_semantic_eval_dataset(dataset)["passed"])

    def test_forbidden_credential_field_is_not_loaded_as_case(self):
        records = [{"id": "bad", "claim": "A", "evidence": "A", "label": "entailment", "api_key": "secret"}]
        dataset = server.build_public_nli_semantic_eval_dataset(records, {
            "dataset_type": "fever",
            "source_url": "https://example.test/public",
            "license": "public",
        })
        self.assertEqual(dataset["cases"], [])
        self.assertTrue(any("forbidden" in e for e in dataset["errors"]))

    def test_summary_runs_stub_eval_without_provider_claim(self):
        summary = server.summarize_public_nli_semantic_eval_readiness(self._dataset())
        self.assertTrue(summary["ready_for_stub_eval"])
        self.assertFalse(summary["is_real_nli_provider_evidence"])
        self.assertFalse(summary["does_semantic_entailment"])
        self.assertEqual(summary["stub_eval_report"]["provider_evidence"], "local_stub")


class CliTest(unittest.TestCase):
    def _cli(self):
        spec_cli = importlib.util.spec_from_file_location(
            "prepare_stage2b_public_nli_eval", ROOT / "tools" / "prepare_stage2b_public_nli_eval.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        return cli

    def test_cli_build_and_validate(self):
        cli = self._cli()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "public_nli_eval.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([
                    "build",
                    "--input", str(FIXTURE),
                    "--output", str(out),
                    "--dataset-type", "snli",
                    "--source-url", "https://nlp.stanford.edu/projects/snli/",
                    "--license", "fixture-only",
                ]), 0)
                self.assertEqual(cli.main(["validate", "--input", str(out), "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
