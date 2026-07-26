"""Phase 2B pluggable reranker skeleton tests.

These tests prove:

1. Default ``search_library`` behavior is unchanged -- reranking is disabled,
   ``result["rerank"]["enabled"]`` is ``False``, the fused RRF order is preserved,
   and no per-item ``rerank`` details are attached (details are absent/inert when
   disabled).
2. The explicit, opt-in deterministic rerank mode reports enabled metadata,
   attaches item-level ``rerank`` details, and REORDERS only the current
   candidates -- it never adds or fetches new chunks.
3. Unknown rerank modes stay disabled with an honest reason and require no
   external service, network access, or credentials.

The reranker is a SKELETON: an in-process, stdlib-only deterministic reranker
(query-term coverage over the already-fused Top K). It is not a real reranking
model, cross-encoder, or semantic judge.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class RerankDefaultOffTest(unittest.TestCase):
    """Reranking is disabled by default and preserves the fused RRF order."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        self._seed()

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _seed(self):
        server.import_document({
            "title": "Alpha Support Policy", "format": "txt",
            "text": "Alpha project shall receive 30 grants in 2026. Beta unrelated text.",
            "source_type": "law_regulation", "status": "effective", "region": "GZ",
            "document_number": "A-2026-1",
        })
        server.import_document({
            "title": "Alpha Field Note", "format": "txt",
            "text": "Alpha field note says 12 teams requested help in 2025.",
            "source_type": "user_fact", "status": "effective", "region": "GZ",
        })

    def test_default_search_has_rerank_disabled(self):
        res = server.search_library("Alpha project 30 grants 2026",
                                    filters={"effective_only": "true"}, limit=5)
        self.assertFalse(res["rerank"]["enabled"])
        self.assertIn("reason", res["rerank"])
        # No per-item rerank details when disabled.
        for item in res["items"]:
            self.assertNotIn("rerank", item)
            self.assertFalse(any(r.startswith("rerank") for r in item["hit_reasons"]))

    def test_default_equals_explicitly_disabled_order(self):
        q = "Alpha project 30 grants 2026"
        base = server.search_library(q, filters={"effective_only": "true"}, limit=5)
        off = server.search_library(q, filters={"effective_only": "true"}, limit=5,
                                    rerank_config={"enabled": False})
        self.assertEqual([i["chunk_id"] for i in base["items"]],
                         [i["chunk_id"] for i in off["items"]])
        self.assertFalse(off["rerank"]["enabled"])

    def test_unknown_mode_stays_disabled(self):
        res = server.search_library("Alpha", rerank_config={"enabled": True, "mode": "provider_api"})
        self.assertFalse(res["rerank"]["enabled"])
        self.assertIn("not available", res["rerank"]["reason"])
        for item in res["items"]:
            self.assertNotIn("rerank", item)


class RerankDeterministicModeTest(unittest.TestCase):
    """Explicit deterministic rerank mode reports metadata and item details."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        server.import_document({
            "title": "Alpha Support Policy", "format": "txt",
            "text": "Alpha project shall receive 30 grants in 2026.",
            "source_type": "law_regulation", "status": "effective", "region": "GZ",
        })
        server.import_document({
            "title": "Alpha Field Note", "format": "txt",
            "text": "Alpha field note says 12 teams requested help in 2025.",
            "source_type": "user_fact", "status": "effective", "region": "GZ",
        })

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_metadata_says_enabled_and_deterministic_local(self):
        res = server.search_library("Alpha project 30 grants 2026",
                                    filters={"effective_only": "true"}, limit=5,
                                    rerank_config={"enabled": True, "mode": "deterministic_local_test"})
        meta = res["rerank"]
        self.assertTrue(meta["enabled"])
        self.assertEqual(meta["mode"], "deterministic_local_test")
        # Honesty guardrail: never claim to be a real reranking model.
        self.assertFalse(meta["is_real_rerank_model"])
        self.assertIn("top_k", meta)

    def test_true_shorthand_enables_deterministic_mode(self):
        res = server.search_library("Alpha", rerank_config=True)
        self.assertTrue(res["rerank"]["enabled"])
        self.assertEqual(res["rerank"]["mode"], "deterministic_local_test")

    def test_items_carry_rerank_details_when_enabled(self):
        res = server.search_library("Alpha project 30 grants 2026",
                                    filters={"effective_only": "true"}, limit=5, rerank_config=True)
        self.assertTrue(res["items"])
        for item in res["items"]:
            self.assertIn("rerank", item)
            self.assertIn("score", item["rerank"])
            self.assertIn("original_rank", item["rerank"])
            self.assertEqual(item["rerank"]["mode"], "deterministic_local_test")
        self.assertTrue(any(r.startswith("rerank_score:")
                            for r in res["items"][0]["hit_reasons"]))

    def test_rerank_only_reorders_does_not_add_chunks(self):
        q = "Alpha project 30 grants 2026"
        filters = {"effective_only": "true"}
        base = server.search_library(q, filters=filters, limit=10)
        reranked = server.search_library(q, filters=filters, limit=10, rerank_config=True)
        # Same set of chunk ids -- reranking must not retrieve anything new.
        self.assertEqual({i["chunk_id"] for i in base["items"]},
                         {i["chunk_id"] for i in reranked["items"]})
        self.assertEqual(len(base["items"]), len(reranked["items"]))

    def test_reranker_reorders_by_score_directly(self):
        """Unit test the reorder logic deterministically with crafted candidates."""
        pipe = server.resolve_rerank_pipeline(True)
        self.assertTrue(pipe.enabled)
        # Two fused items in fused order [A, B]; B has full query-term coverage,
        # A has none. The reranker must promote B above A.
        items = [
            {"chunk_id": "A", "content": "unrelated text", "hit_reasons": [], "channels": {}},
            {"chunk_id": "B", "content": "alpha grants 2026", "hit_reasons": [], "channels": {}},
        ]
        out = pipe.apply("alpha grants 2026", items)
        self.assertEqual([i["chunk_id"] for i in out], ["B", "A"])
        self.assertGreater(out[0]["rerank"]["score"], out[1]["rerank"]["score"])
        # original_rank reflects pre-rerank position (B was 2nd).
        self.assertEqual(out[0]["rerank"]["original_rank"], 2)

    def test_reranker_is_deterministic(self):
        q = "Alpha project 30 grants 2026"
        filters = {"effective_only": "true"}
        a = server.search_library(q, filters=filters, limit=10, rerank_config=True)
        b = server.search_library(q, filters=filters, limit=10, rerank_config=True)
        self.assertEqual([(i["chunk_id"], round(i["rerank"]["score"], 9)) for i in a["items"]],
                         [(i["chunk_id"], round(i["rerank"]["score"], 9)) for i in b["items"]])

    def test_disabled_pipeline_apply_is_inert(self):
        pipe = server.resolve_rerank_pipeline(None)
        self.assertFalse(pipe.enabled)
        items = [{"chunk_id": "A", "content": "alpha", "hit_reasons": [], "channels": {}}]
        out = pipe.apply("alpha", items)
        self.assertEqual([i["chunk_id"] for i in out], ["A"])
        self.assertNotIn("rerank", out[0])


class RerankEvalTest(unittest.TestCase):
    """The eval harness reports honest rerank state and needs no rerank model."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        server.import_document({
            "title": "Alpha Support Policy", "format": "txt",
            "text": "Alpha project shall receive 30 grants in 2026.",
            "source_type": "law_regulation", "status": "effective", "region": "GZ",
        })

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_eval_default_rerank_disabled(self):
        report = server.evaluate_retrieval_cases(
            [{"id": "c1", "query": "Alpha", "relevant_titles": ["Alpha Support Policy"]}], k=5)
        self.assertFalse(report["rerank"]["enabled"])

    def test_eval_reports_enabled_when_opted_in(self):
        report = server.evaluate_retrieval_cases(
            [{"id": "c1", "query": "Alpha", "relevant_titles": ["Alpha Support Policy"]}],
            k=5, rerank_config=True)
        self.assertTrue(report["rerank"]["enabled"])
        self.assertEqual(report["rerank"]["mode"], "deterministic_local_test")


class RerankParamHelperTest(unittest.TestCase):
    """The HTTP ?rerank= param only ever selects the offline test reranker."""

    def test_enable_tokens(self):
        for tok in ("1", "true", "TRUE", "yes", "on", "test", "deterministic_local_test"):
            cfg = server._rerank_config_from_param(tok)
            self.assertEqual(cfg, {"enabled": True, "mode": "deterministic_local_test"},
                             f"token {tok!r} should enable")

    def test_non_enable_tokens_stay_off(self):
        for tok in ("", "0", "false", "no", "provider_api", "nonsense", None):
            self.assertIsNone(server._rerank_config_from_param(tok), f"token {tok!r} should stay off")


if __name__ == "__main__":
    unittest.main()
