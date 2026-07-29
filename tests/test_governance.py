"""Stage 6 governance (encryption/backup/restore/retention/audit) skeleton tests."""

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

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class EncryptionPolicyTest(unittest.TestCase):
    def test_default_is_disabled_and_metadata_only(self):
        pol = server.build_encryption_policy()
        self.assertEqual(pol["method"], "encryption_policy_v1")
        self.assertEqual(pol["algorithm"], "none")
        self.assertEqual(pol["status"], "disabled")

    def test_declared_algorithm_and_key_source(self):
        pol = server.build_encryption_policy({"algorithm": "aes-256-gcm", "key_source": "kms"})
        self.assertEqual(pol["status"], "declared")
        self.assertEqual(pol["key_source"], "kms")
        self.assertTrue(pol["at_rest_enabled"])

    def test_no_key_value_ever_in_policy(self):
        pol = server.build_encryption_policy({"algorithm": "aes-256-gcm", "key": "SUPERSECRET"})
        blob = json.dumps(pol, ensure_ascii=False)
        self.assertNotIn("SUPERSECRET", blob)
        for field in server.GOVERNANCE_FORBIDDEN_KEY_FIELDS:
            self.assertNotIn(field, pol)

    def test_validate_rejects_key_material(self):
        report = server.validate_encryption_policy(
            {"algorithm": "aes-256-gcm", "key_value": "abc"})
        self.assertFalse(report["passed"])
        self.assertTrue(any("key material" in e for e in report["errors"]))

    def test_validate_rejects_unknown_algorithm(self):
        report = server.validate_encryption_policy({"algorithm": "rot13"})
        self.assertFalse(report["passed"])


class BackupManifestTest(unittest.TestCase):
    def test_manifest_is_deterministic_over_content(self):
        a = server.build_backup_manifest([{"name": "s", "content": {"x": 1, "y": 2}}],
                                         {"created_at": "2026-07-01T00:00:00"})
        b = server.build_backup_manifest([{"name": "s", "content": {"y": 2, "x": 1}}],
                                         {"created_at": "2026-07-01T00:00:00"})
        self.assertEqual(a["manifest_checksum"], b["manifest_checksum"])
        self.assertTrue(a["entries"][0]["checksum"].startswith("sha256:"))

    def test_manifest_validates(self):
        m = server.build_backup_manifest([{"name": "drafts", "content": {"a": 1}}])
        self.assertTrue(server.validate_backup_manifest(m)["passed"])

    def test_duplicate_entry_name_fails(self):
        m = server.build_backup_manifest([{"name": "dup", "content": "1"},
                                          {"name": "dup", "content": "2"}])
        report = server.validate_backup_manifest(m)
        self.assertFalse(report["passed"])
        self.assertTrue(any("duplicate name" in e for e in report["errors"]))

    def test_empty_manifest_fails(self):
        m = server.build_backup_manifest([])
        self.assertFalse(server.validate_backup_manifest(m)["passed"])


class RestorePlanTest(unittest.TestCase):
    def test_plan_has_step_per_entry(self):
        m = server.build_backup_manifest([{"name": "a", "content": "1"},
                                          {"name": "b", "content": "2"}])
        plan = server.build_restore_plan(m)
        self.assertEqual(plan["step_count"], 2)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["steps"][0]["action"], "verify_and_restore")
        self.assertTrue(server.validate_restore_plan(plan)["passed"])

    def test_missing_checksum_warns_and_marks_unverified(self):
        m = server.build_backup_manifest([{"name": "a", "checksum": ""}])
        plan = server.build_restore_plan(m)
        self.assertTrue(any("integrity cannot be confirmed" in w for w in plan["warnings"]))
        self.assertEqual(plan["steps"][0]["action"], "restore_unverified")

    def test_empty_manifest_plan_warns(self):
        plan = server.build_restore_plan({"entries": []})
        self.assertEqual(plan["step_count"], 0)
        self.assertTrue(any("no restorable entries" in w for w in plan["warnings"]))


class RetentionPolicyTest(unittest.TestCase):
    def test_deletion_candidates_by_age(self):
        pol = server.build_retention_policy({
            "days": {"draft": 30},
            "artifacts": [{"type": "draft", "id": "d1", "age_days": 40},
                          {"type": "draft", "id": "d2", "age_days": 10}],
        })
        self.assertEqual(pol["deletion_candidate_count"], 1)
        self.assertEqual(pol["deletion_candidates"][0]["id"], "d1")
        self.assertEqual(pol["deletion_candidates"][0]["over_by_days"], 10)

    def test_defaults_present_for_all_artifact_types(self):
        pol = server.build_retention_policy()
        for atype in server.GOVERNANCE_ARTIFACT_TYPES:
            self.assertIn(atype, pol["retention_days"])
        self.assertTrue(server.validate_retention_policy(pol)["passed"])

    def test_validate_rejects_negative_days(self):
        report = server.validate_retention_policy({"retention_days": {"draft": -5}})
        self.assertFalse(report["passed"])

    def test_nothing_is_deleted(self):
        # The builder only reports candidates; it exposes no delete action.
        pol = server.build_retention_policy({"artifacts": [{"type": "draft", "id": "x", "age_days": 9999}]})
        self.assertIn("nothing is deleted", pol["boundary"])


class AuditRecordTest(unittest.TestCase):
    def test_record_builds_and_validates(self):
        rec = server.build_audit_record({"actor": "demo", "action": "export",
                                         "workspace_id": "ws-1", "resource": "draft:1"})
        self.assertEqual(rec["method"], "audit_record_v1")
        self.assertTrue(rec["timestamp"])  # auto-filled
        self.assertTrue(server.validate_audit_record(rec)["passed"])

    def test_missing_actor_or_action_fails(self):
        self.assertFalse(server.validate_audit_record({"action": "x", "timestamp": "t"})["passed"])
        self.assertFalse(server.validate_audit_record({"actor": "y", "timestamp": "t"})["passed"])

    def test_unknown_result_fails(self):
        rec = server.build_audit_record({"actor": "a", "action": "b"})
        rec["result"] = "maybe"
        self.assertFalse(server.validate_audit_record(rec)["passed"])

    def test_secret_field_rejected(self):
        report = server.validate_audit_record(
            {"actor": "a", "action": "b", "timestamp": "t", "password": "p"})
        self.assertFalse(report["passed"])

    def test_missing_workspace_warns(self):
        rec = server.build_audit_record({"actor": "a", "action": "b"})
        report = server.validate_audit_record(rec)
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("workspace_id" in w for w in report["warnings"]))


class GovernanceHttpTest(unittest.TestCase):
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

    def _get(self, path):
        with self.opener.open(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_policy_endpoint(self):
        status, body = self._get("/api/governance/policy")
        self.assertEqual(status, 200)
        self.assertEqual(body["method"], "governance_policy_v1")
        self.assertIn("encryption", body)
        self.assertIn("retention", body)

    def test_audit_validate_ok(self):
        status, body = self._post("/api/governance/audit/validate",
                                  {"actor": "demo", "action": "export", "timestamp": "2026-07-01T00:00:00",
                                   "workspace_id": "ws-1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["passed"])

    def test_audit_validate_rejects_missing_actor(self):
        status, body = self._post("/api/governance/audit/validate",
                                  {"action": "export", "timestamp": "2026-07-01T00:00:00"})
        self.assertEqual(status, 422)
        self.assertFalse(body["passed"])


class GovernanceFrontendTest(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.appjs = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    def test_index_has_governance_controls(self):
        self.assertIn('id="loadGovernanceBtn"', self.index)
        self.assertIn('id="governanceStatus"', self.index)
        self.assertIn("不含密钥值", self.index)

    def test_app_js_wires_governance(self):
        for token in ("loadGovernancePolicy", "/api/governance/policy", "retention_days"):
            self.assertIn(token, self.appjs, f"app.js missing {token}")


if __name__ == "__main__":
    unittest.main()
