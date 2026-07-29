"""Stage 6 model-provider data-flow disclosure + risk grading skeleton tests."""

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


class ProviderProfileTest(unittest.TestCase):
    def test_default_is_offline_local_no_egress(self):
        prof = server.build_provider_profile()
        self.assertEqual(prof["method"], "provider_profile_v1")
        self.assertEqual(prof["mode"], "offline")
        self.assertTrue(prof["is_local"])
        self.assertEqual(prof["data_categories_sent"], [])

    def test_local_mode_drops_declared_categories(self):
        prof = server.build_provider_profile({"mode": "local_only", "data_categories": ["personal_data"]})
        self.assertEqual(prof["data_categories_sent"], [])
        self.assertFalse(prof["stores_data"])
        self.assertFalse(prof["trains_on_data"])

    def test_external_keeps_known_categories_only(self):
        prof = server.build_provider_profile({
            "mode": "external_api", "data_categories": ["prompt_text", "bogus_cat", "personal_data"]})
        self.assertIn("prompt_text", prof["data_categories_sent"])
        self.assertIn("personal_data", prof["data_categories_sent"])
        self.assertNotIn("bogus_cat", prof["data_categories_sent"])

    def test_profile_never_carries_credentials(self):
        prof = server.build_provider_profile({"mode": "external_api", "api_key": "SECRET", "endpoint_url": "https://x"})
        blob = json.dumps(prof, ensure_ascii=False)
        self.assertNotIn("SECRET", blob)
        for field in server.PROVIDER_FORBIDDEN_FIELDS:
            self.assertNotIn(field, prof)


class ProviderRiskGradeTest(unittest.TestCase):
    def test_offline_is_low_risk(self):
        prof = server.build_provider_profile({"mode": "offline"})
        risk = server.grade_provider_risk(prof)
        self.assertEqual(risk["level"], "low")
        self.assertIn("local_mode_no_egress", risk["reasons"])

    def test_external_prompt_only_is_medium(self):
        prof = server.build_provider_profile({"mode": "openai_compatible", "data_categories": ["prompt_text"]})
        risk = server.grade_provider_risk(prof)
        self.assertEqual(risk["level"], "medium")
        self.assertEqual(risk["score"], 2)  # +1 sends + +1 residency unknown

    def test_external_sensitive_store_train_is_high(self):
        prof = server.build_provider_profile({
            "mode": "external_api", "data_categories": ["prompt_text", "personal_data"],
            "stores_data": True, "trains_on_data": True, "data_residency": "us"})
        risk = server.grade_provider_risk(prof)
        self.assertEqual(risk["level"], "high")
        self.assertGreaterEqual(risk["score"], 5)
        self.assertTrue(any("sensitive_categories" in r for r in risk["reasons"]))

    def test_unknown_mode_is_blocked(self):
        risk = server.grade_provider_risk({"mode": "psychic"})
        self.assertEqual(risk["level"], "blocked")
        self.assertIn("unknown_mode", risk["reasons"])

    def test_invalid_profile_is_blocked(self):
        risk = server.grade_provider_risk("not-a-profile")
        self.assertEqual(risk["level"], "blocked")

    def test_grading_is_deterministic(self):
        prof = server.build_provider_profile({"mode": "external_api", "data_categories": ["evidence_text"],
                                             "stores_data": True})
        self.assertEqual(server.grade_provider_risk(prof), server.grade_provider_risk(prof))


class ProviderDisclosureTest(unittest.TestCase):
    def test_local_disclosure_says_no_egress(self):
        prof = server.build_provider_profile({"mode": "offline"})
        disc = server.build_provider_disclosure(prof)
        self.assertTrue(disc["is_local"])
        self.assertFalse(disc["sends_data_externally"])
        self.assertIn("不向任何外部供应商发送数据", disc["disclosure_text"])

    def test_external_disclosure_lists_categories_and_flags(self):
        prof = server.build_provider_profile({
            "mode": "external_api", "data_categories": ["prompt_text", "personal_data"],
            "stores_data": True, "trains_on_data": True})
        disc = server.build_provider_disclosure(prof)
        self.assertTrue(disc["sends_data_externally"])
        self.assertIn("prompt_text", disc["disclosure_text"])
        self.assertIn("personal_data", disc["disclosure_text"])
        self.assertIn("存储：是", disc["disclosure_text"])
        self.assertIn("用于训练：是", disc["disclosure_text"])


class ProviderValidationTest(unittest.TestCase):
    def test_valid_external_profile_passes(self):
        prof = server.build_provider_profile({"mode": "external_api", "data_categories": ["prompt_text"]})
        self.assertTrue(server.validate_provider_profile(prof)["passed"])

    def test_credential_field_rejected(self):
        report = server.validate_provider_profile({"mode": "external_api", "api_key": "x"})
        self.assertFalse(report["passed"])
        self.assertTrue(any("credential/endpoint field" in e for e in report["errors"]))

    def test_unsupported_mode_rejected(self):
        report = server.validate_provider_profile({"mode": "carrier_pigeon"})
        self.assertFalse(report["passed"])

    def test_unknown_category_rejected(self):
        report = server.validate_provider_profile({"mode": "external_api", "data_categories_sent": ["nope"]})
        self.assertFalse(report["passed"])

    def test_external_without_categories_warns(self):
        report = server.validate_provider_profile({"mode": "external_api", "data_categories_sent": []})
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(any("no data categories" in w for w in report["warnings"]))


class ProviderSummaryTest(unittest.TestCase):
    def test_summary_bundles_profile_disclosure_risk(self):
        summ = server.build_provider_risk_summary({"mode": "external_api", "data_categories": ["prompt_text"]})
        self.assertEqual(summ["method"], "provider_risk_summary_v1")
        self.assertIn("profile", summ)
        self.assertIn("disclosure", summ)
        self.assertIn("risk", summ)

    def test_summary_never_leaks_supplied_secret(self):
        summ = server.build_provider_risk_summary({"mode": "external_api", "api_key": "SECRET",
                                                   "data_categories": ["prompt_text"]})
        self.assertNotIn("SECRET", json.dumps(summ, ensure_ascii=False))


class ProviderHttpTest(unittest.TestCase):
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

    def test_get_risk_default_low(self):
        status, body = self._get("/api/providers/risk")
        self.assertEqual(status, 200)
        self.assertEqual(body["risk"]["level"], "low")

    def test_grade_external_high(self):
        status, body = self._post("/api/providers/risk/grade", {
            "mode": "external_api", "data_categories": ["prompt_text", "confidential_material"],
            "stores_data": True, "trains_on_data": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["risk"]["level"], "high")

    def test_grade_rejects_credential_field(self):
        status, body = self._post("/api/providers/risk/grade", {"mode": "external_api", "api_key": "x"})
        self.assertEqual(status, 422)
        self.assertFalse(body["passed"])


class ProviderFrontendTest(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.appjs = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    def test_index_has_provider_risk_controls(self):
        self.assertIn('id="loadProviderRiskBtn"', self.index)
        self.assertIn('id="providerRiskStatus"', self.index)
        self.assertIn("数据流", self.index)

    def test_app_js_wires_provider_risk(self):
        for token in ("loadProviderRisk", "/api/providers/risk", "disclosure_text"):
            self.assertIn(token, self.appjs, f"app.js missing {token}")


if __name__ == "__main__":
    unittest.main()
