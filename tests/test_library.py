import base64
import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class EvidenceLibraryTest(unittest.TestCase):
    def setUp(self):
        # Redirect the module DB to a fresh temp file per test.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    # --- schema ---------------------------------------------------------
    def test_schema_init_is_idempotent(self):
        conn = server.db()
        server.init_schema(conn)  # second call must not raise
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertTrue({"documents", "evidence_chunks", "import_jobs"} <= names)

    # --- txt / html / docx import --------------------------------------
    def test_import_txt(self):
        res = server.import_document({
            "title": "测试政策", "format": "txt",
            "text": "第一段内容。\n\n第二段内容。",
            "source_url": "https://example.gov.cn/a", "status": "有效",
        })
        self.assertEqual(res["status"], "succeeded")
        self.assertEqual(res["doc_status"], "effective")
        self.assertGreaterEqual(res["chunk_count"], 2)

    def test_import_html_strips_tags(self):
        html = "<html><body><p>正文一</p><script>ignore()</script><p>正文二</p></body></html>".encode("utf-8")
        res = server.import_document({
            "title": "网页", "format": "html",
            "content_base64": base64.b64encode(html).decode(),
        })
        self.assertEqual(res["status"], "succeeded")
        doc = server.get_document(res["document_id"])
        self.assertIn("正文一", doc["content"])
        self.assertNotIn("ignore", doc["content"])

    def test_import_docx(self):
        raw = server.export_docx("文件标题", "第一段。\n\n第二段。")
        res = server.import_document({
            "title": "docx", "format": "docx",
            "content_base64": base64.b64encode(raw).decode(),
        })
        self.assertEqual(res["status"], "succeeded")
        doc = server.get_document(res["document_id"])
        self.assertIn("第一段", doc["content"])

    # --- dedupe ---------------------------------------------------------
    def test_sha256_dedupe(self):
        payload = {"title": "重复", "format": "txt", "text": "相同的正文内容。"}
        first = server.import_document(dict(payload))
        second = server.import_document(dict(payload))
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["document_id"], first["document_id"])
        self.assertEqual(len(server.list_documents()), 1)

    def test_sha256_unique_constraint_enforced(self):
        # The race-hardening in import_document relies on this DB-level guarantee.
        first = server.import_document({"title": "唯一约束", "format": "txt", "text": "唯一内容。"})
        conn = server.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("dup-id", "副本", "", "", "", "", "effective", "txt",
                     first["sha256"], 10, "x", "2026-01-01T00:00:00"),
                )
                conn.commit()
        finally:
            conn.close()

    def test_integrity_error_falls_back_to_duplicate(self):
        # Simulate the race: the pre-check SELECT misses, but the UNIQUE index
        # still rejects the second INSERT; import must return duplicate, not
        # raise (which would surface as an HTTP 500). We patch our own db()
        # seam with a thin connection proxy that neutralizes ONLY the first
        # sha256 pre-check SELECT — sqlite3.Connection is a C type and cannot
        # be monkeypatched at the class level.
        first = server.import_document({"title": "竞态", "format": "txt", "text": "竞态内容。"})
        real_db = server.db
        state = {"skipped": False}

        class _EmptyResult:
            def fetchone(self):
                return None

        class _ConnProxy:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if (not state["skipped"] and sql.strip().upper().startswith(
                        "SELECT ID FROM DOCUMENTS WHERE SHA256")):
                    state["skipped"] = True
                    return _EmptyResult()
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        server.db = lambda: _ConnProxy(real_db())
        try:
            second = server.import_document({"title": "竞态2", "format": "txt", "text": "竞态内容。"})
        finally:
            server.db = real_db
        self.assertTrue(state["skipped"], "pre-check SELECT was not exercised")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["document_id"], first["document_id"])
        self.assertEqual(len(server.list_documents()), 1)
        self.assertEqual(len(server.list_jobs("duplicate")), 1)

    # --- PDF must fail explicitly (quarantine) --------------------------
    def test_pdf_is_quarantined_not_silent(self):
        res = server.import_document({
            "title": "政策.pdf", "format": "pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4 fake").decode(),
        })
        self.assertEqual(res["status"], "quarantined")
        self.assertTrue(res["quarantined"])
        self.assertEqual(res["error_code"], "unsupported_format")
        self.assertEqual(len(server.list_documents()), 0)

    # --- chunk status ---------------------------------------------------
    def test_chunk_status_prohibited_for_repealed(self):
        res = server.import_document({
            "title": "废止文件", "format": "txt",
            "text": "废止后的正文。", "status": "已废止",
        })
        chunks = server.list_chunks(res["document_id"])
        self.assertTrue(chunks)
        self.assertTrue(all(c["status"] == "prohibited" for c in chunks))

    def test_chunk_status_override(self):
        res = server.import_document({
            "title": "仅参考", "format": "txt",
            "text": "参考内容。", "status": "有效", "chunk_status": "reference_only",
        })
        chunks = server.list_chunks(res["document_id"])
        self.assertTrue(all(c["status"] == "reference_only" for c in chunks))

    def test_chunk_offsets_are_stable(self):
        res = server.import_document({"title": "定位", "format": "txt", "text": "定位测试正文。"})
        doc = server.get_document(res["document_id"])
        for c in server.list_chunks(res["document_id"]):
            self.assertEqual(doc["content"][c["char_start"]:c["char_end"]], c["content"])

    # --- failed jobs are queryable -------------------------------------
    def test_failed_jobs_queryable(self):
        server.import_document({"title": "空", "format": "txt", "text": "   "})
        server.import_document({"title": "坏docx", "format": "docx",
                                "content_base64": base64.b64encode(b"not a zip").decode()})
        server.import_document({"title": "扫描件.pdf", "format": "pdf", "text": "x"})
        failed = server.list_jobs("failed")
        quarantined = server.list_jobs("quarantined")
        self.assertGreaterEqual(len(failed), 2)
        self.assertEqual(len(quarantined), 1)
        for job in failed + quarantined:
            self.assertTrue(job["error_reason"])

    def test_jobs_list_all(self):
        server.import_document({"title": "ok", "format": "txt", "text": "正文。"})
        self.assertEqual(len(server.list_jobs()), 1)


class EvidenceLibraryHTTPTest(unittest.TestCase):
    """End-to-end tests over the real HTTP handler on an ephemeral local port."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        # Bypass any host proxy for local 127.0.0.1 calls. On Windows a
        # configured system proxy would otherwise intercept these requests and
        # return 503. Empty ProxyHandler = direct connection.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _url(self, path):
        return f"http://127.0.0.1:{self._port}{path}"

    def _post(self, path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self._url(path), data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _get(self, path):
        try:
            with self._opener.open(self._url(path), timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_full_http_flow(self):
        status, res = self._post("/api/library/import", {
            "title": "HTTP政策", "format": "txt",
            "text": "第一段正文。\n\n第二段正文。",
            "source_url": "https://example.gov.cn/http", "status": "有效",
        })
        self.assertEqual(status, 201)
        self.assertEqual(res["status"], "succeeded")
        doc_id = res["document_id"]

        status, docs = self._get("/api/library/documents")
        self.assertEqual(status, 200)
        self.assertTrue(any(d["id"] == doc_id for d in docs["items"]))

        status, doc = self._get(f"/api/library/document?id={doc_id}")
        self.assertEqual(status, 200)
        self.assertIn("第一段正文", doc["content"])

        status, chunks = self._get(f"/api/library/chunks?document_id={doc_id}")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(chunks["items"]), 2)
        self.assertTrue(all(c["status"] == "citable" for c in chunks["items"]))

    def test_http_unknown_document_404(self):
        status, _ = self._get("/api/library/document?id=does-not-exist")
        self.assertEqual(status, 404)

    def test_http_jobs_queryable(self):
        ok_status, _ = self._post("/api/library/import",
                                  {"title": "好文件", "format": "txt", "text": "正文内容。"})
        self.assertEqual(ok_status, 201)
        pdf_status, pdf_res = self._post("/api/library/import", {
            "title": "扫描.pdf", "format": "pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4").decode(),
        })
        self.assertEqual(pdf_status, 422)
        self.assertEqual(pdf_res["status"], "quarantined")

        status, jobs = self._get("/api/library/jobs")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(jobs["items"]), 2)

        status, quarantined = self._get("/api/library/jobs?status=quarantined")
        self.assertEqual(status, 200)
        self.assertEqual(len(quarantined["items"]), 1)
        self.assertTrue(quarantined["items"][0]["quarantined"])
        self.assertEqual(quarantined["items"][0]["error_code"], "unsupported_format")


if __name__ == "__main__":
    unittest.main()
