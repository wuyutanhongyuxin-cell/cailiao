"""Stage 6 RBAC / workspaces / minimum-permission skeleton tests."""

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


class AccessContextTest(unittest.TestCase):
    def test_default_is_least_privilege_viewer(self):
        ctx = server.build_access_context()
        self.assertEqual(ctx["method"], "access_context_v1")
        self.assertEqual(ctx["user"]["role"], "viewer")
        self.assertEqual(ctx["allowed_actions"], ["read"])
        self.assertTrue(ctx["is_demo"])
        self.assertEqual(ctx["auth"], "none")

    def test_unknown_role_falls_back_to_viewer(self):
        ctx = server.build_access_context({"role": "superuser"})
        self.assertEqual(ctx["user"]["role"], "viewer")

    def test_owner_has_all_actions(self):
        ctx = server.build_access_context({"role": "owner"})
        self.assertEqual(set(ctx["allowed_actions"]), set(server.RBAC_ACTIONS))

    def test_workspace_defaults_and_override(self):
        ctx = server.build_access_context({"role": "editor"}, {"id": "ws-1", "name": "项目一"})
        self.assertEqual(ctx["workspace"]["id"], "ws-1")
        self.assertEqual(ctx["workspace"]["name"], "项目一")

    def test_context_carries_no_credentials(self):
        ctx = server.build_access_context({"role": "admin"})
        self.assertNotIn("MATERIAL_LLM_API_KEY", json.dumps(ctx, ensure_ascii=False))


class CheckPermissionTest(unittest.TestCase):
    def test_viewer_read_only(self):
        ctx = server.build_access_context({"role": "viewer"})
        self.assertTrue(server.check_permission(ctx, "read")["allowed"])
        for action in ("generate", "review", "export", "manage_library", "manage_users", "manage_config"):
            res = server.check_permission(ctx, action)
            self.assertFalse(res["allowed"], action)
            self.assertEqual(res["reason"], "role_not_permitted")

    def test_admin_can_manage_users_and_config(self):
        ctx = server.build_access_context({"role": "admin"})
        self.assertTrue(server.check_permission(ctx, "manage_users")["allowed"])
        self.assertTrue(server.check_permission(ctx, "manage_config")["allowed"])

    def test_editor_can_generate_not_manage_users(self):
        ctx = server.build_access_context({"role": "editor"})
        self.assertTrue(server.check_permission(ctx, "generate")["allowed"])
        self.assertFalse(server.check_permission(ctx, "manage_users")["allowed"])

    def test_reviewer_can_review_not_generate(self):
        ctx = server.build_access_context({"role": "reviewer"})
        self.assertTrue(server.check_permission(ctx, "review")["allowed"])
        self.assertFalse(server.check_permission(ctx, "generate")["allowed"])

    def test_unknown_action_denied(self):
        ctx = server.build_access_context({"role": "owner"})
        res = server.check_permission(ctx, "launch_missiles")
        self.assertFalse(res["allowed"])
        self.assertEqual(res["reason"], "unknown_action")

    def test_workspace_isolation(self):
        ctx = server.build_access_context({"role": "owner"}, {"id": "ws-1"})
        same = server.check_permission(ctx, "read", {"workspace_id": "ws-1"})
        other = server.check_permission(ctx, "read", {"workspace_id": "ws-2"})
        self.assertTrue(same["allowed"])
        self.assertFalse(other["allowed"])
        self.assertEqual(other["reason"], "workspace_mismatch")

    def test_invalid_context_denied(self):
        res = server.check_permission("not-a-context", "read")
        self.assertFalse(res["allowed"])
        self.assertEqual(res["reason"], "invalid_context")


class AccessPolicyValidationTest(unittest.TestCase):
    def test_valid_context_passes(self):
        ctx = server.build_access_context({"role": "editor"}, {"id": "ws-1"})
        report = server.validate_access_policy(ctx)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["role"], "editor")

    def test_unsupported_role_fails(self):
        report = server.validate_access_policy(
            {"user": {"role": "wizard"}, "workspace": {"id": "ws-1"}})
        self.assertFalse(report["passed"])
        self.assertTrue(any("unsupported role" in e for e in report["errors"]))

    def test_missing_workspace_fails(self):
        report = server.validate_access_policy({"user": {"role": "viewer"}})
        self.assertFalse(report["passed"])
        self.assertTrue(any("workspace" in e for e in report["errors"]))

    def test_tampered_allowed_actions_fails(self):
        ctx = server.build_access_context({"role": "viewer"}, {"id": "ws-1"})
        ctx["allowed_actions"] = ["read", "manage_users"]  # not viewer's canonical set
        report = server.validate_access_policy(ctx)
        self.assertFalse(report["passed"])
        self.assertTrue(any("canonical matrix" in e for e in report["errors"]))

    def test_unknown_action_in_allowed_fails(self):
        ctx = server.build_access_context({"role": "owner"}, {"id": "ws-1"})
        ctx["allowed_actions"] = list(ctx["allowed_actions"]) + ["teleport"]
        report = server.validate_access_policy(ctx)
        self.assertFalse(report["passed"])
        self.assertTrue(any("unknown action" in e for e in report["errors"]))

    def test_non_object_fails_safely(self):
        report = server.validate_access_policy(["nope"])
        self.assertFalse(report["passed"])


class AccessHttpTest(unittest.TestCase):
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

    def test_context_endpoint_default_viewer(self):
        status, body = self._get("/api/access/context")
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["role"], "viewer")
        self.assertEqual(body["allowed_actions"], ["read"])

    def test_context_endpoint_role_preview(self):
        status, body = self._get("/api/access/context?role=admin")
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["role"], "admin")
        self.assertIn("manage_users", body["allowed_actions"])

    def test_validate_endpoint_rejects_bad_role(self):
        status, body = self._post("/api/access/validate",
                                  {"user": {"role": "nope"}, "workspace": {"id": "ws-1"}})
        self.assertEqual(status, 422)
        self.assertFalse(body["passed"])

    def test_check_endpoint_enforces_role(self):
        status, body = self._post("/api/access/check",
                                  {"user": {"role": "viewer"}, "workspace": {"id": "ws-1"},
                                   "action": "manage_users"})
        self.assertEqual(status, 200)
        self.assertFalse(body["allowed"])
        self.assertEqual(body["reason"], "role_not_permitted")


class AccessFrontendTest(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.appjs = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    def test_index_has_access_controls(self):
        for token in ('id="accessRole"', 'id="applyRoleBtn"', 'id="accessStatus"', 'id="accessActions"'):
            self.assertIn(token, self.index)
        # Honest boundary: no fake login UI.
        self.assertIn("无登录", self.index)

    def test_app_js_wires_access(self):
        for token in ("loadAccessContext", "renderAccessContext", "/api/access/context", "allowed_actions"):
            self.assertIn(token, self.appjs, f"app.js missing {token}")


if __name__ == "__main__":
    unittest.main()
