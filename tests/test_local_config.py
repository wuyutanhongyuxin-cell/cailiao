"""Stage 6 local configuration + offline model option skeleton tests."""

import importlib.util
import json
import os
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class LocalConfigDefaultsTest(unittest.TestCase):
    def test_default_config_is_offline_and_safe(self):
        cfg = server.build_local_config()
        self.assertEqual(cfg["method"], "local_config_v1")
        self.assertEqual(cfg["model_mode"], "offline")
        self.assertTrue(cfg["offline"])
        self.assertFalse(cfg["allow_network"])
        self.assertIn("不读取 .env", cfg["offline_notice"])

    def test_overrides_select_supported_mode(self):
        cfg = server.build_local_config({"model_mode": "prompt_only"})
        self.assertEqual(cfg["model_mode"], "prompt_only")
        self.assertTrue(cfg["offline"])
        cfg2 = server.build_local_config({"model_mode": "openai_compatible"})
        self.assertFalse(cfg2["offline"])
        self.assertTrue(cfg2["allow_network"])

    def test_unknown_mode_falls_back_to_offline(self):
        cfg = server.build_local_config({"model_mode": "definitely-not-a-mode"})
        self.assertEqual(cfg["model_mode"], "offline")

    def test_provider_configured_is_boolean_only(self):
        cfg = server.build_local_config()
        self.assertIsInstance(cfg["provider_configured"], bool)
        # The config must never carry credential values.
        blob = json.dumps(cfg, ensure_ascii=False)
        self.assertNotIn("MATERIAL_LLM_API_KEY", blob)


class LocalConfigValidationTest(unittest.TestCase):
    def test_valid_offline_config_passes(self):
        report = server.validate_local_config({"model_mode": "offline"})
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(report["offline"])

    def test_missing_mode_fails(self):
        report = server.validate_local_config({})
        self.assertFalse(report["passed"])
        self.assertTrue(any("model_mode" in e for e in report["errors"]))

    def test_unsupported_mode_fails(self):
        report = server.validate_local_config({"model_mode": "telepathy"})
        self.assertFalse(report["passed"])
        self.assertTrue(any("unsupported model_mode" in e for e in report["errors"]))

    def test_non_bool_pref_fails(self):
        report = server.validate_local_config({"model_mode": "offline", "save_draft_locally": "yes"})
        self.assertFalse(report["passed"])
        self.assertTrue(any("save_draft_locally" in e for e in report["errors"]))

    def test_online_without_provider_warns(self):
        # Ensure no provider env is set for this assertion.
        old_base = os.environ.pop("MATERIAL_LLM_BASE_URL", None)
        old_key = os.environ.pop("MATERIAL_LLM_API_KEY", None)
        try:
            report = server.validate_local_config({"model_mode": "openai_compatible"})
            self.assertTrue(report["passed"], report["errors"])
            self.assertTrue(any("fall back to prompt_only" in w for w in report["warnings"]))
        finally:
            if old_base is not None:
                os.environ["MATERIAL_LLM_BASE_URL"] = old_base
            if old_key is not None:
                os.environ["MATERIAL_LLM_API_KEY"] = old_key


class OfflineModelCallTest(unittest.TestCase):
    def test_offline_mode_makes_no_network_call(self):
        result = server.call_llm("严格提示词内容", {"model_mode": "offline"})
        self.assertEqual(result["mode"], "offline")
        self.assertFalse(result["network_used"])
        self.assertIn("严格提示词内容", result["draft"])
        self.assertIn("离线模式", result["draft"])

    def test_prompt_only_mode_makes_no_network_call(self):
        result = server.call_llm("提示词", {"model_mode": "prompt_only"})
        self.assertEqual(result["mode"], "prompt_only")
        self.assertFalse(result["network_used"])
        self.assertEqual(result["draft"], "")

    def test_offline_never_calls_urlopen(self):
        # Hard guarantee: monkeypatch urlopen to explode; offline must not touch it.
        original = urllib.request.urlopen

        def boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("offline mode attempted a network call")

        urllib.request.urlopen = boom
        try:
            for mode in ("offline", "prompt_only"):
                res = server.call_llm("p", {"model_mode": mode})
                self.assertFalse(res["network_used"])
        finally:
            urllib.request.urlopen = original


class LocalConfigHttpTest(unittest.TestCase):
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

    def test_get_config_returns_offline_default(self):
        status, body = self._get("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(body["model_mode"], "offline")
        self.assertFalse(body["allow_network"])

    def test_validate_config_ok(self):
        status, body = self._post("/api/config/validate", {"model_mode": "offline"})
        self.assertEqual(status, 200)
        self.assertTrue(body["passed"])

    def test_validate_config_rejects_bad_mode(self):
        status, body = self._post("/api/config/validate", {"model_mode": "nope"})
        self.assertEqual(status, 422)
        self.assertFalse(body["passed"])

    def test_generate_offline_reports_no_network(self):
        status, body = self._post("/api/generate", {
            "genre": "通知", "title": "关于示例工作的通知",
            "fields": {}, "facts": "示例事实", "draft": "",
            "config": {"model_mode": "offline"},
        })
        self.assertEqual(status, 200)
        # Either blocked by analysis or offline generation; if generated, must be offline.
        if body.get("mode") not in ("blocked",):
            self.assertEqual(body["mode"], "offline")
            self.assertFalse(body["network_used"])


class SettingsFrontendTest(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.appjs = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    def test_index_has_settings_panel_and_mode_selector(self):
        self.assertIn('data-panel="settings"', self.index)
        self.assertIn('id="modelMode"', self.index)
        self.assertIn('id="applyConfigBtn"', self.index)
        self.assertIn("离线", self.index)

    def test_app_js_wires_config(self):
        for token in ("loadConfig", "applyConfig", "/api/config", "/api/config/validate", "mws_model_mode"):
            self.assertIn(token, self.appjs, f"app.js missing {token}")


if __name__ == "__main__":
    unittest.main()
