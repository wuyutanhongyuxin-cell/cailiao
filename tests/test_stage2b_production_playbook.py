"""Stage 2B production-playbook status / readiness-matrix tests.

Honesty guard: the default playbook status must not be ready-for-rollout, must
mirror the five external blockers, and must never auto-check a ROADMAP parent.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
PLAYBOOK_DOC = ROOT / "docs" / "STAGE2B_PRODUCTION_PLAYBOOK.md"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

EXPECTED_BLOCKERS = {
    "real_query_set", "real_query_bm25_calibration",
    "real_embedding_provider_vector_store", "real_reranker_rrf", "real_nli_semantic_conflict",
}
EXPECTED_URLS = (
    "https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion",
    "https://qdrant.tech/documentation/search/hybrid-queries/",
    "https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html",
    "https://github.com/beir-cellar/beir",
)


class PlaybookStatusTest(unittest.TestCase):
    def test_default_not_ready_for_rollout(self):
        r = server.build_stage2b_production_playbook_status()
        self.assertFalse(r["ready_for_real_provider_rollout"])
        self.assertFalse(r["roadmap_parent_items_checked"])

    def test_mirrors_the_five_external_blockers(self):
        r = server.build_stage2b_production_playbook_status()
        audit_ids = {b["id"] for b in r["external_dependency_audit"]["blockers"]}
        self.assertEqual(audit_ids, EXPECTED_BLOCKERS)
        # Every phase references one of those blocker ids.
        for p in r["phases"]:
            self.assertIn(p["blocker_id"], EXPECTED_BLOCKERS)
        self.assertTrue(all(not p["ready"] for p in r["phases"]))

    def test_required_metrics_present(self):
        r = server.build_stage2b_production_playbook_status()
        for m in ("ndcg@k", "mrr", "map", "recall@k", "precision@k", "latency_p95"):
            self.assertIn(m, r["required_metrics"])

    def test_references_present(self):
        r = server.build_stage2b_production_playbook_status()
        for url in EXPECTED_URLS:
            self.assertIn(url, r["references"])

    def test_acceptance_gates_present(self):
        r = server.build_stage2b_production_playbook_status()
        self.assertTrue(r["acceptance_gates"])

    def test_json_serializable(self):
        json.dumps(server.build_stage2b_production_playbook_status(), ensure_ascii=False)

    def test_ready_only_when_all_blockers_satisfied_metadata_only(self):
        # Build a fully-declared config (declared metadata shape only) and confirm
        # rollout can flip true WITHOUT ever checking a ROADMAP parent.
        def _case(i):
            return {"id": f"q{i:03d}", "query": f"query {i}",
                    "provenance": {"source": "intake", "collected_at": "2026-07-01T00:00:00", "anonymized": True},
                    "relevant_titles": [f"t{i}"]}
        def _doc(i):
            return {"title": f"t{i}", "text": f"body {i}", "format": "txt", "status": "valid"}
        cfg = {"artifacts": {
            "real_query_set": {"metadata": {"name": "real set"}, "cases": [_case(i) for i in range(50)]},
            "real_query_bm25_calibration": {"metadata": {"name": "real set"},
                                            "cases": [_case(i) for i in range(50)],
                                            "corpus": [_doc(i) for i in range(50)]},
            "real_embedding_provider_vector_store": {
                "provider": {"provider": "openai", "model": "m", "dim": 8, "credential_source": "K"},
                "store": {"backend": "qdrant", "backup_configured": True}, "index": {"metric": "cosine"}},
            "real_reranker_rrf": {
                "provider": {"provider": "cohere", "model": "r", "credential_source": "K", "eval_metrics": ["ndcg"]},
                "rrf": {"rank_constant": 60, "rank_window_size": 100, "channels": ["bm25"]}},
            "real_nli_semantic_conflict": {
                "provider": {"provider": "hf", "model": "deberta", "credential_source": "K"},
                "eval_labels": ["entailment", "contradiction", "neutral"],
                "policy": {"verdict_labels": ["supports", "refutes", "not_enough_info"],
                           "min_confidence": 0.7, "block_on": ["refutes"]}},
        }}
        r = server.build_stage2b_production_playbook_status(cfg)
        self.assertTrue(r["ready_for_real_provider_rollout"])
        self.assertFalse(r["roadmap_parent_items_checked"])  # still never auto-checks parents


class PlaybookDocTest(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(PLAYBOOK_DOC.exists())

    def test_doc_includes_all_four_source_urls(self):
        text = PLAYBOOK_DOC.read_text(encoding="utf-8")
        for url in EXPECTED_URLS:
            self.assertIn(url, text)

    def test_doc_states_planning_only(self):
        text = PLAYBOOK_DOC.read_text(encoding="utf-8").lower()
        self.assertIn("planning", text)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_stage2b_production_playbook", ROOT / "tools" / "check_stage2b_production_playbook.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_one(self):
        self.assertEqual(self._run(["--json"]), 1)


if __name__ == "__main__":
    unittest.main()

