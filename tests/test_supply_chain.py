"""Stage 6 dependency inventory / SBOM / container-plan skeleton tests."""

import importlib.util
import json
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"
SBOM_FILE = ROOT / "docs" / "sbom.json"
REQUIREMENTS = ROOT / "requirements.txt"
CONTAINERFILE = ROOT / "Containerfile"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

FORBIDDEN = ("api_key", "secret", "password", "token", "authorization", "private_key", "passphrase")


class DependencyInventoryTest(unittest.TestCase):
    def test_inventory_is_stdlib_only_and_deterministic(self):
        a = server.build_dependency_inventory()
        b = server.build_dependency_inventory()
        self.assertEqual(a, b)  # deterministic
        self.assertTrue(a["stdlib_only"])
        self.assertEqual(a["runtime_dependencies"], [])
        self.assertTrue(a["python_requires"].startswith(">="))

    def test_inventory_validates(self):
        self.assertTrue(server.validate_dependency_inventory(server.build_dependency_inventory())["passed"])

    def test_unpinned_dependency_fails(self):
        report = server.validate_dependency_inventory({
            "project": "x", "version": "1", "python_requires": ">=3.12",
            "runtime_dependencies": [{"name": "requests"}]})  # missing version
        self.assertFalse(report["passed"])
        self.assertTrue(any("pinned" in e for e in report["errors"]))

    def test_credential_field_rejected(self):
        report = server.validate_dependency_inventory({
            "project": "x", "version": "1", "python_requires": ">=3.12",
            "runtime_dependencies": [], "api_key": "leak"})
        self.assertFalse(report["passed"])


class SbomDocumentTest(unittest.TestCase):
    def test_sbom_has_project_version_components(self):
        sbom = server.build_sbom_document()
        self.assertTrue(sbom["project"])
        self.assertTrue(sbom["version"])
        self.assertGreater(sbom["component_count"], 0)
        self.assertTrue(server.validate_sbom_document(sbom)["passed"])

    def test_sbom_is_deterministic_and_timestamp_free(self):
        a = json.dumps(server.build_sbom_document(), ensure_ascii=False, sort_keys=True)
        b = json.dumps(server.build_sbom_document(), ensure_ascii=False, sort_keys=True)
        self.assertEqual(a, b)
        # No time-valued FIELD is embedded (a boundary string may mention the word).
        sbom = server.build_sbom_document()
        for key in ("timestamp", "created_at", "generated_at", "date"):
            self.assertNotIn(key, sbom)

    def test_sbom_has_no_secrets(self):
        blob = json.dumps(server.build_sbom_document(), ensure_ascii=False)
        for field in FORBIDDEN:
            self.assertNotIn(field, blob)

    def test_committed_sbom_file_matches_helper(self):
        self.assertTrue(SBOM_FILE.exists(), "docs/sbom.json must exist")
        on_disk = json.loads(SBOM_FILE.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, server.build_sbom_document(),
                         "docs/sbom.json is out of sync with build_sbom_document()")

    def test_empty_sbom_fails_validation(self):
        self.assertFalse(server.validate_sbom_document({"project": "x", "version": "1", "components": []})["passed"])


class ContainerPlanTest(unittest.TestCase):
    def test_plan_defaults_are_offline_and_no_execution(self):
        plan = server.build_container_deploy_plan()
        self.assertFalse(plan["enabled"])  # optional, off by default
        self.assertEqual(plan["network_default"], "none")
        self.assertEqual(plan["expose_port"], 8000)
        self.assertTrue(plan["steps"])
        self.assertTrue(server.validate_container_deploy_plan(plan)["passed"])

    def test_custom_port_and_image(self):
        plan = server.build_container_deploy_plan({"port": 9001, "base_image": "python:3.12-alpine", "enabled": True})
        self.assertEqual(plan["expose_port"], 9001)
        self.assertEqual(plan["base_image"], "python:3.12-alpine")
        self.assertTrue(plan["enabled"])

    def test_invalid_port_rejected_by_validator(self):
        plan = server.build_container_deploy_plan()
        plan["expose_port"] = 70000
        self.assertFalse(server.validate_container_deploy_plan(plan)["passed"])


class SupplyChainFilesTest(unittest.TestCase):
    def test_requirements_exists_and_has_no_pinned_third_party(self):
        self.assertTrue(REQUIREMENTS.exists())
        text = REQUIREMENTS.read_text(encoding="utf-8")
        # Only comments/blank lines — no actual `pkg==x.y` requirement lines.
        pkg_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        self.assertEqual(pkg_lines, [])

    def test_containerfile_exists_and_has_no_install(self):
        self.assertTrue(CONTAINERFILE.exists())
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("EXPOSE", text)
        # No ACTIVE install/build directive (comments explaining the absence are fine).
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            self.assertNotIn("pip install", stripped, "Containerfile has an active pip install line")
            self.assertFalse(stripped.upper().startswith("RUN "), "Containerfile should have no RUN build step")

    def test_no_secrets_in_supply_chain_files(self):
        for f in (REQUIREMENTS, CONTAINERFILE, SBOM_FILE):
            blob = f.read_text(encoding="utf-8").lower()
            for token in ("api_key", "password=", "secret=", "bearer "):
                self.assertNotIn(token, blob, f"{f.name} contains {token}")


class SupplyChainHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        cls.thread = Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_sbom_endpoint(self):
        with self.opener.open(f"http://127.0.0.1:{self.port}/api/supply-chain/sbom", timeout=10) as r:
            status, body = r.status, json.loads(r.read().decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(body["method"], "supply_chain_summary_v1")
        self.assertIn("dependency_inventory", body)
        self.assertIn("sbom", body)
        self.assertIn("container_deploy_plan", body)


class SupplyChainFrontendTest(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.appjs = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    def test_index_has_sbom_controls(self):
        self.assertIn('id="loadSbomBtn"', self.index)
        self.assertIn('id="sbomStatus"', self.index)
        self.assertIn("SBOM", self.index)

    def test_app_js_wires_sbom(self):
        for token in ("loadSbom", "/api/supply-chain/sbom", "runtime_dependencies"):
            self.assertIn(token, self.appjs, f"app.js missing {token}")


if __name__ == "__main__":
    unittest.main()
