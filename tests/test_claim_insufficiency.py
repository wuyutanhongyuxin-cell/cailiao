"""Phase 2B evidence-insufficiency / refusal detail (v1) tests.

Prove that ``verify_claim`` always returns a structured, deterministic
``insufficiency`` object explaining WHY a claim is not safely supported, and that
it stays honest: it is lexical audit metadata, NOT semantic entailment or NLI.

Covered:
- no retrieved evidence -> has_insufficiency, summary no_retrieved_evidence;
- required markers missing -> missing markers surfaced top-level and in a detail;
- deterministic conflict downgrade -> conflict_count + blocking true;
- supported / clean -> has_insufficiency false, summary none, blocking false;
- weak lexical support -> weak_lexical_overlap, explained as lexical not semantic;
- HTTP /api/library/verify-claim response includes insufficiency;
- the helper is deterministic and standard-library only.
"""

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class ClaimInsufficiencyTest(unittest.TestCase):
    """Deterministic insufficiency object over the helper + verify_claim."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _imp(self, title, text, **kw):
        payload = {"title": title, "format": "txt", "text": text,
                   "status": "effective", "source_type": "law_regulation"}
        payload.update(kw)
        return server.import_document(payload)

    def test_no_evidence_reports_insufficiency(self):
        # Empty library: nothing can be retrieved.
        res = server.verify_claim("Some unseen policy grants 30 awards in 2026.",
                                  filters={"effective_only": "true"})
        self.assertEqual(res["status"], "unsupported")
        ins = res["insufficiency"]
        self.assertTrue(ins["has_insufficiency"])
        self.assertEqual(ins["summary"], "no_retrieved_evidence")
        self.assertTrue(ins["blocking"])
        self.assertEqual(ins["method"], "deterministic_lexical_insufficiency_v1")
        self.assertTrue(any(d["code"] == "no_retrieved_evidence" for d in ins["details"]))

    def test_missing_marker_surfaced_top_level_and_in_detail(self):
        self._imp("Alpha Support", "Alpha project shall receive 30 grants in 2026.")
        res = server.verify_claim("Alpha project shall receive 31 grants in 2026.",
                                  filters={"effective_only": "true"}, limit=10)
        # Top-level missing_markers still populated (backward compatible).
        self.assertIn("31", res["missing_markers"])
        ins = res["insufficiency"]
        self.assertTrue(ins["has_insufficiency"])
        self.assertEqual(ins["summary"], "required_markers_missing")
        self.assertTrue(ins["blocking"])
        self.assertIn("31", ins["missing_markers"])
        detail = next(d for d in ins["details"] if d["code"] == "required_markers_missing")
        self.assertIn("31", detail["markers"])

    def test_conflict_downgrade_reports_conflict_count_and_blocking(self):
        self._imp("Alpha Support", "Alpha project shall receive 30 grants in 2026.")
        self._imp("Alpha Conflict", "Alpha project shall receive 31 grants in 2026.")
        res = server.verify_claim("Alpha project shall receive 30 grants in 2026.",
                                  filters={"effective_only": "true"}, limit=10)
        self.assertEqual(res["status"], "needs_verification")
        ins = res["insufficiency"]
        self.assertTrue(ins["has_insufficiency"])
        self.assertEqual(ins["summary"], "conflict_candidates_found")
        self.assertTrue(ins["blocking"])
        self.assertGreaterEqual(ins["conflict_count"], 1)
        detail = next(d for d in ins["details"] if d["code"] == "conflict_candidates_found")
        self.assertTrue(detail["downgraded_from_supported"])

    def test_supported_clean_has_no_insufficiency(self):
        self._imp("Gamma Policy", "Gamma policy provides 45 service windows in 2026.")
        res = server.verify_claim("Gamma policy provides 45 service windows in 2026.",
                                  filters={"effective_only": "true"}, limit=10)
        self.assertEqual(res["status"], "supported")
        ins = res["insufficiency"]
        self.assertFalse(ins["has_insufficiency"])
        self.assertEqual(ins["summary"], "none")
        self.assertFalse(ins["blocking"])
        self.assertEqual(ins["conflict_count"], 0)
        self.assertEqual(ins["details"], [])

    def test_weak_lexical_overlap_is_explained_as_lexical(self):
        # A pure-text claim (no required markers) with thin overlap -> not supported,
        # and the insufficiency object must frame it as lexical, not semantic.
        self._imp("Beta Note", "Beta gamma delta policy overview text.")
        res = server.verify_claim("Beta zzzzz", filters={"effective_only": "true"}, limit=10)
        self.assertNotEqual(res["status"], "supported")
        ins = res["insufficiency"]
        self.assertTrue(ins["has_insufficiency"])
        self.assertEqual(ins["summary"], "weak_lexical_overlap")
        detail = next(d for d in ins["details"] if d["code"] == "weak_lexical_overlap")
        self.assertIn("lexical", detail["message"])
        self.assertIn("not semantic", detail["message"])

    def test_helper_is_deterministic(self):
        evidence_map = {"required_markers": ["30"], "missing_markers": ["30"],
                        "covered_markers": {}, "supporting_items": [], "coverage_ratio": 0.0}
        conflict = {"has_conflicts": False, "items": [], "summary": "no_deterministic_conflict_found",
                    "method": "deterministic_lexical_v1"}
        args = dict(status="needs_verification", items=[{"content": "x"}], evidence_map=evidence_map,
                    conflict_evidence=conflict, claim_tokens={"a", "b"}, overlap={"a"},
                    downgraded_by_conflict=False)
        a = server.build_evidence_insufficiency(**args)
        b = server.build_evidence_insufficiency(**args)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(a["summary"], "required_markers_missing")


class ClaimInsufficiencyHTTPTest(unittest.TestCase):
    """The HTTP verify-claim response naturally includes insufficiency."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        # Bypass any ambient proxy for localhost calls.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self._port}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self._opener.open(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_http_verify_claim_includes_insufficiency(self):
        self._post("/api/library/import", {
            "title": "Search Policy", "format": "txt",
            "text": "Gamma policy provides 45 service windows in 2026.",
            "source_type": "law_regulation", "status": "effective", "region": "HZ",
        })
        status, verify = self._post("/api/library/verify-claim", {
            "claim": "Gamma policy provides 45 service windows in 2026.",
            "filters": {"effective_only": "true"},
        })
        self.assertEqual(status, 200)
        self.assertIn("insufficiency", verify)
        ins = verify["insufficiency"]
        # Survives JSON round-trip with the expected shape.
        for key in ("has_insufficiency", "summary", "blocking", "missing_markers",
                    "conflict_count", "overlap", "details", "method"):
            self.assertIn(key, ins)
        self.assertEqual(verify["status"], "supported")
        self.assertFalse(ins["has_insufficiency"])
        self.assertEqual(ins["summary"], "none")

    def test_http_verify_claim_no_evidence_blocking(self):
        status, verify = self._post("/api/library/verify-claim", {
            "claim": "Totally unseen policy grants 77 awards in 2027.",
            "filters": {"effective_only": "true"},
        })
        self.assertEqual(status, 200)
        ins = verify["insufficiency"]
        self.assertTrue(ins["has_insufficiency"])
        self.assertTrue(ins["blocking"])
        self.assertEqual(ins["summary"], "no_retrieved_evidence")


if __name__ == "__main__":
    unittest.main()
