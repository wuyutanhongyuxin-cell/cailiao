"""Final external-dependency audit / gate tests.

Honesty guard: the default audit must report all five real-world blockers as
outstanding and must never auto-check a ROADMAP parent item. Any "satisfied"
result is declared-metadata-shape only, never live validation.
"""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

EXPECTED_IDS = {
    "real_query_set", "real_query_bm25_calibration",
    "real_embedding_provider_vector_store", "real_reranker_rrf", "real_nli_semantic_conflict",
}


def _case(i):
    return {"id": f"q{i:03d}", "query": f"查询{i}",
            "provenance": {"source": "intake", "collected_at": "2026-07-01T00:00:00", "anonymized": True},
            "relevant_titles": [f"t{i}"]}


def _doc(i):
    return {"title": f"t{i}", "text": f"正文{i}", "format": "txt", "status": "有效"}


def _complete_config():
    real50 = {"metadata": {"name": "real set"}, "cases": [_case(i) for i in range(50)]}
    real50c = {"metadata": {"name": "real set"}, "cases": [_case(i) for i in range(50)],
               "corpus": [_doc(i) for i in range(50)]}
    vec = {"provider": {"provider": "openai", "model": "text-embedding-3-large", "dim": 3072,
                        "credential_source": "OPENAI_API_KEY"},
           "store": {"backend": "qdrant", "backup_configured": True},
           "index": {"metric": "cosine", "type": "hnsw"}}
    rer = {"provider": {"provider": "cohere", "model": "rerank-3.5", "credential_source": "COHERE_API_KEY",
                        "eval_metrics": ["ndcg", "mrr"]},
           "rrf": {"rank_constant": 60, "rank_window_size": 100, "channels": ["bm25", "vector"]}}
    nli = {"provider": {"provider": "huggingface", "model": "microsoft/deberta-base-mnli",
                        "credential_source": "HF_TOKEN"},
           "eval_labels": ["entailment", "contradiction", "neutral"],
           "policy": {"verdict_labels": ["supports", "refutes", "not_enough_info"],
                      "min_confidence": 0.7, "block_on": ["refutes"]}}
    return {"artifacts": {
        "real_query_set": real50,
        "real_query_bm25_calibration": real50c,
        "real_embedding_provider_vector_store": vec,
        "real_reranker_rrf": rer,
        "real_nli_semantic_conflict": nli,
    }}


class DefaultAuditTest(unittest.TestCase):
    def test_default_has_exactly_five_outstanding_blockers(self):
        a = server.build_external_dependency_audit()
        self.assertEqual(a["blocker_count"], 5)
        self.assertEqual({b["id"] for b in a["blockers"]}, EXPECTED_IDS)
        self.assertFalse(a["all_external_dependencies_satisfied"])
        self.assertEqual(set(a["outstanding_ids"]), EXPECTED_IDS)
        self.assertEqual(a["satisfied_ids"], [])

    def test_default_does_not_check_roadmap_parents(self):
        a = server.build_external_dependency_audit()
        self.assertFalse(a["roadmap_parent_items_checked"])

    def test_each_blocker_documents_input_state_and_gate(self):
        a = server.build_external_dependency_audit()
        for b in a["blockers"]:
            for field in ("roadmap_line", "topic", "required_external_input",
                          "current_repo_state", "protected_by", "verification_mode"):
                self.assertIn(field, b)
            self.assertEqual(b["verification_mode"], "declared_metadata_shape_only")

    def test_output_is_json_serializable(self):
        json.dumps(server.build_external_dependency_audit(), ensure_ascii=False)


class DeclaredConfigAuditTest(unittest.TestCase):
    def test_complete_declared_metadata_can_satisfy_but_is_metadata_only(self):
        a = server.build_external_dependency_audit(_complete_config())
        self.assertTrue(a["all_external_dependencies_satisfied"])
        # Even when satisfied, no ROADMAP parent item is auto-checked, and the
        # boundary states this is declared-metadata shape only, not live validation.
        self.assertFalse(a["roadmap_parent_items_checked"])
        self.assertIn("declared", a["boundary"].lower())
        for b in a["blockers"]:
            self.assertEqual(b["verification_mode"], "declared_metadata_shape_only")

    def test_dropping_corpus_reopens_only_bm25_blocker(self):
        cfg = copy.deepcopy(_complete_config())
        del cfg["artifacts"]["real_query_bm25_calibration"]["corpus"]
        a = server.build_external_dependency_audit(cfg)
        self.assertFalse(a["all_external_dependencies_satisfied"])
        self.assertEqual(a["outstanding_ids"], ["real_query_bm25_calibration"])

    def test_partial_config_leaves_others_outstanding(self):
        cfg = {"artifacts": {"real_query_set": _complete_config()["artifacts"]["real_query_set"]}}
        a = server.build_external_dependency_audit(cfg)
        self.assertIn("real_query_set", a["satisfied_ids"])
        self.assertEqual(len(a["outstanding_ids"]), 4)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_external_dependency_audit", ROOT / "tools" / "check_external_dependency_audit.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_complete_declared_config_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_config(), f, ensure_ascii=False)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
