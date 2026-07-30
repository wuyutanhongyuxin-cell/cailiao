"""Stage 2 real embedding-provider / vector-store production-readiness scaffold tests.

Honesty guard: no real provider or persistent store exists in this repo. Tests
build DECLARED configs in memory to exercise the readiness gate; a "ready" verdict
means the config is complete, never that a provider/store was contacted.
"""

import contextlib
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


def _complete_config():
    return {
        "provider": {"provider": "openai", "model": "text-embedding-3-large",
                     "dim": 3072, "credential_source": "OPENAI_API_KEY"},
        "store": {"backend": "postgres_pgvector", "backup_configured": True,
                  "persistence_path": "/var/lib/vec"},
        "index": {"metric": "cosine", "type": "hnsw"},
    }


class ProviderReadinessTest(unittest.TestCase):
    def test_default_is_local_test_not_real(self):
        r = server.build_embedding_provider_readiness()
        self.assertTrue(r["is_local_test_embedder"])
        self.assertFalse(r["is_real_provider_declared"])

    def test_local_test_mode_not_real(self):
        r = server.build_embedding_provider_readiness({"mode": "deterministic_local_test"})
        self.assertTrue(r["is_local_test_embedder"])
        self.assertFalse(r["is_real_provider_declared"])

    def test_real_provider_needs_model_dim_credential_source(self):
        r = server.build_embedding_provider_readiness({"provider": "openai"})
        self.assertFalse(r["is_real_provider_declared"])
        self.assertTrue(r["missing"])  # model/dim/credential_source missing

    def test_complete_real_provider_declared(self):
        r = server.build_embedding_provider_readiness(_complete_config()["provider"])
        self.assertTrue(r["is_real_provider_declared"])
        self.assertEqual(r["missing"], [])

    def test_credential_value_field_rejected(self):
        report = server.validate_embedding_provider_config(
            {"provider": "openai", "model": "m", "dim": 8, "api_key": "sk-xxx"})
        self.assertFalse(report["passed"])
        self.assertTrue(any("credential/endpoint value field" in e for e in report["errors"]))

    def test_credential_source_is_env_name_not_value(self):
        # A credential SOURCE (env var name) is fine and does not leak a secret.
        cfg = _complete_config()["provider"]
        report = server.validate_embedding_provider_config(cfg)
        self.assertTrue(report["passed"], report["errors"])
        self.assertNotIn("api_key", cfg)


class VectorStorePlanTest(unittest.TestCase):
    def test_default_is_in_memory_not_persistent(self):
        plan = server.build_vector_store_plan()
        self.assertFalse(plan["is_persistent"])

    def test_in_memory_rejected_for_production(self):
        report = server.validate_vector_store_plan({"backend": "in_memory"})
        self.assertFalse(report["passed"])
        self.assertTrue(any("not production-ready" in e for e in report["errors"]))

    def test_persistent_backend_passes(self):
        report = server.validate_vector_store_plan({"backend": "qdrant", "backup_configured": True})
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(report["is_persistent"])

    def test_persistent_without_backup_warns(self):
        plan = server.build_vector_store_plan({"backend": "milvus", "backup_configured": False})
        plan["is_persistent"] = True
        report = server.validate_vector_store_plan(plan)
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("without backup" in w for w in report["warnings"]))

    def test_credential_field_rejected(self):
        report = server.validate_vector_store_plan({"backend": "qdrant", "password": "p"})
        self.assertFalse(report["passed"])


class VectorIndexReadinessTest(unittest.TestCase):
    def test_default_repo_state_not_production_ready(self):
        r = server.build_vector_index_readiness()
        self.assertFalse(r["production_ready"])
        self.assertTrue(r["missing"])
        self.assertFalse(r["current_shipped_state"]["production_ready"])
        self.assertFalse(r["current_shipped_state"]["is_real_embedding_model"])

    def test_complete_declared_config_is_ready_without_network(self):
        r = server.build_vector_index_readiness(_complete_config())
        self.assertTrue(r["production_ready"], r["missing"])
        self.assertEqual(r["missing"], [])

    def test_real_provider_but_in_memory_store_not_ready(self):
        cfg = _complete_config()
        cfg["store"] = {"backend": "in_memory"}
        r = server.build_vector_index_readiness(cfg)
        self.assertFalse(r["production_ready"])
        self.assertTrue(any("persistent vector store" in m for m in r["missing"]))

    def test_missing_index_descriptor_not_ready(self):
        cfg = _complete_config()
        cfg.pop("index")
        r = server.build_vector_index_readiness(cfg)
        self.assertFalse(r["production_ready"])
        self.assertTrue(any("index descriptor" in m for m in r["missing"]))

    def test_readiness_is_json_serializable(self):
        json.dumps(server.build_vector_index_readiness(_complete_config()), ensure_ascii=False)


class CliTest(unittest.TestCase):
    def _run(self, args):
        spec_cli = importlib.util.spec_from_file_location(
            "check_vector_production_readiness", ROOT / "tools" / "check_vector_production_readiness.py")
        cli = importlib.util.module_from_spec(spec_cli)
        spec_cli.loader.exec_module(cli)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(args)

    def test_cli_default_exits_nonzero(self):
        self.assertEqual(self._run(["--json"]), 1)

    def test_cli_complete_config_exits_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(_complete_config(), f)
            path = f.name
        self.assertEqual(self._run(["--config", path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
