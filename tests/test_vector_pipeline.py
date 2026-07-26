"""Phase 2B vector retrieval / replaceable embedding pipeline skeleton tests.

These tests prove two things:

1. Default ``search_library`` behavior is unchanged -- the vector channel is
   disabled, ``result["vector"]["enabled"]`` is ``False``, and lexical/BM25
   rankings/payload shape are preserved (backward compatible).
2. The explicit, opt-in deterministic vector test mode enables a ``vector`` RRF
   channel with per-item rank/score and ``vector_*`` hit reasons, identifies
   itself as a local/deterministic (NOT real) embedder, and requires no external
   service, network access, or credentials.

The vector pipeline is a SKELETON: an in-process, stdlib-only deterministic
embedder (signed feature hashing) plus a brute-force cosine index. It is not a
real semantic model, vector database, or reranker.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class VectorPipelineDefaultOffTest(unittest.TestCase):
    """Vector retrieval is disabled by default and preserves lexical/BM25 v1."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

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

    def test_default_search_has_vector_disabled(self):
        self._seed()
        res = server.search_library("Alpha project 30 grants 2026",
                                    filters={"effective_only": "true"}, limit=5)
        # Backward-compatible flag other code/tests depend on.
        self.assertFalse(res["vector"]["enabled"])
        self.assertIn("reason", res["vector"])
        # No vector channel is fused, and lexical/BM25 channels are intact.
        self.assertNotIn("vector", res["channels"])
        self.assertTrue(res["items"])
        top = res["items"][0]
        self.assertEqual(top["document_title"], "Alpha Support Policy")
        self.assertIn("lexical_exact", top["channels"])
        self.assertIn("fts_or_ngram", top["channels"])
        self.assertNotIn("vector", top["channels"])
        # No vector_* hit reasons leak into the default path.
        self.assertFalse(any(r.startswith("vector") for r in top["hit_reasons"]))

    def test_default_equals_explicitly_disabled(self):
        self._seed()
        q = "Alpha project 30 grants 2026"
        base = server.search_library(q, filters={"effective_only": "true"}, limit=5)
        off = server.search_library(q, filters={"effective_only": "true"}, limit=5,
                                    vector_config={"enabled": False})
        # Ranking and fused scores are byte-for-byte the same when vector is off.
        self.assertEqual([(i["chunk_id"], round(i["fused_score"], 9)) for i in base["items"]],
                         [(i["chunk_id"], round(i["fused_score"], 9)) for i in off["items"]])
        self.assertFalse(off["vector"]["enabled"])

    def test_falsy_and_unknown_modes_stay_disabled(self):
        self._seed()
        for cfg in (None, False, 0, "", {"enabled": False},
                    {"enabled": True, "mode": "provider_api"}):
            res = server.search_library("Alpha", vector_config=cfg)
            self.assertFalse(res["vector"]["enabled"], f"cfg {cfg!r} should be disabled")
            self.assertNotIn("vector", res["channels"])
        # Unknown/unavailable modes explain themselves honestly (no silent enable,
        # no network attempt).
        res = server.search_library("Alpha", vector_config={"enabled": True, "mode": "provider_api"})
        self.assertIn("not available", res["vector"]["reason"])


class VectorPipelineDeterministicModeTest(unittest.TestCase):
    """Explicit deterministic local test mode wires a real vector RRF channel."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
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

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_metadata_says_enabled_and_deterministic_local(self):
        res = server.search_library(
            "Alpha project 30 grants 2026", filters={"effective_only": "true"}, limit=5,
            vector_config={"enabled": True, "mode": "deterministic_local_test"})
        meta = res["vector"]
        self.assertTrue(meta["enabled"])
        self.assertEqual(meta["mode"], "deterministic_local_test")
        # Honesty guardrail: never claim to be a real embedding model.
        self.assertFalse(meta["is_real_embedding_model"])
        self.assertIn("dim", meta)

    def test_true_shorthand_enables_deterministic_mode(self):
        res = server.search_library("Alpha", vector_config=True)
        self.assertTrue(res["vector"]["enabled"])
        self.assertEqual(res["vector"]["mode"], "deterministic_local_test")

    def test_vector_channel_contributes_rank_score_and_reason(self):
        res = server.search_library(
            "Alpha project 30 grants 2026", filters={"effective_only": "true"}, limit=5,
            vector_config=True)
        self.assertIn("vector", res["channels"])
        self.assertTrue(res["items"])
        # At least one returned item carries the vector channel with rank+score
        # and a vector hit reason.
        vec_items = [i for i in res["items"] if "vector" in i["channels"]]
        self.assertTrue(vec_items, "no item carried the vector channel")
        hit = vec_items[0]
        self.assertIn("rank", hit["channels"]["vector"])
        self.assertIn("score", hit["channels"]["vector"])
        self.assertGreater(hit["channels"]["vector"]["score"], 0.0)
        self.assertTrue(any(r.startswith("vector_sim:") for r in hit["hit_reasons"]))
        self.assertTrue(any(r.startswith("vector_mode:") for r in hit["hit_reasons"]))

    def test_deterministic_repeatable_scores(self):
        q = "Alpha project 30 grants 2026"
        a = server.search_library(q, filters={"effective_only": "true"}, limit=5, vector_config=True)
        b = server.search_library(q, filters={"effective_only": "true"}, limit=5, vector_config=True)
        self.assertEqual([(i["chunk_id"], round(i["fused_score"], 9)) for i in a["items"]],
                         [(i["chunk_id"], round(i["fused_score"], 9)) for i in b["items"]])

    def test_no_network_or_credentials_required(self):
        # The deterministic embedder must not read env/credentials or hit the
        # network. We assert on behavior: it works with the process's env stripped
        # of any provider-style variables and produces a stable embedding.
        import os
        saved = {k: os.environ.pop(k) for k in list(os.environ)
                 if k.startswith(("MATERIAL_LLM", "OPENAI", "ANTHROPIC")) }
        try:
            emb = server.DeterministicHashEmbedder(dim=64)
            v1 = emb.embed("Alpha project 2026")
            v2 = emb.embed("Alpha project 2026")
            self.assertEqual(v1, v2)
            self.assertEqual(len(v1), 64)
            # Unit-normalized.
            self.assertAlmostEqual(sum(x * x for x in v1), 1.0, places=6)
            # Identical text => cosine 1.0; disjoint text => lower.
            self.assertAlmostEqual(server.DeterministicHashEmbedder.cosine(v1, v2), 1.0, places=6)
            other = emb.embed("completely different beta gamma delta")
            self.assertLess(server.DeterministicHashEmbedder.cosine(v1, other),
                            server.DeterministicHashEmbedder.cosine(v1, v2))
        finally:
            os.environ.update(saved)


class VectorPipelineEvalTest(unittest.TestCase):
    """The eval harness reports honest vector state and needs no embeddings."""

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

    def test_eval_default_vector_disabled(self):
        report = server.evaluate_retrieval_cases(
            [{"id": "c1", "query": "Alpha", "relevant_titles": ["Alpha Support Policy"]}], k=5)
        self.assertFalse(report["vector"]["enabled"])

    def test_eval_reports_enabled_when_opted_in(self):
        report = server.evaluate_retrieval_cases(
            [{"id": "c1", "query": "Alpha", "relevant_titles": ["Alpha Support Policy"]}],
            k=5, vector_config=True)
        self.assertTrue(report["vector"]["enabled"])
        self.assertEqual(report["vector"]["mode"], "deterministic_local_test")


class VectorParamHelperTest(unittest.TestCase):
    """The HTTP ?vector= param only ever selects the offline test channel."""

    def test_enable_tokens(self):
        for tok in ("1", "true", "TRUE", "yes", "on", "test", "deterministic_local_test"):
            cfg = server._vector_config_from_param(tok)
            self.assertEqual(cfg, {"enabled": True, "mode": "deterministic_local_test"},
                             f"token {tok!r} should enable")

    def test_non_enable_tokens_stay_off(self):
        for tok in ("", "0", "false", "no", "provider_api", "nonsense", None):
            self.assertIsNone(server._vector_config_from_param(tok), f"token {tok!r} should stay off")


if __name__ == "__main__":
    unittest.main()
