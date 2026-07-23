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

import io
import zipfile


def build_xlsx(rows, sheet_name="Sheet1"):
    """Build a minimal valid .xlsx (OOXML) with shared strings for the given rows.

    rows: list[list[str]] of cell text. Returns raw bytes.
    """
    strings = []
    index = {}
    for row in rows:
        for cell in row:
            if cell not in index:
                index[cell] = len(strings)
                strings.append(cell)
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
          '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
               '</Relationships>')
    sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">'
           + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    sheet_rows = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, cell in enumerate(row):
            col = chr(ord("A") + ci)
            cells.append(f'<c r="{col}{ri}" t="s"><v>{index[cell]}</v></c>')
        sheet_rows.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>' + "".join(sheet_rows) + '</sheetData></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


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
        # Use named columns so this targets the UNIQUE(sha256) constraint rather
        # than column-count mismatches as the schema grows (Phase 1B).
        first = server.import_document({"title": "唯一约束", "format": "txt", "text": "唯一内容。"})
        conn = server.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO documents (id, title, status, format, sha256, char_count, "
                    "content, imported_at) VALUES (?,?,?,?,?,?,?,?)",
                    ("dup-id", "副本", "effective", "txt", first["sha256"], 10, "x",
                     "2026-01-01T00:00:00"),
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


class EvidenceLibraryPhase1BTest(unittest.TestCase):
    """Phase 1B: authority ranking, version linking, incremental update, XLSX, location."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    # --- authority ranking ---------------------------------------------
    def test_source_type_normalization_and_authority(self):
        self.assertEqual(server.normalize_source_type("法律法规"), "law_regulation")
        self.assertEqual(server.normalize_source_type("ministry"), "ministry")
        self.assertEqual(server.normalize_source_type("乱写"), "unknown")
        self.assertEqual(server.authority_level_for("law_regulation"), 6)
        self.assertGreater(server.authority_level_for("state_council"),
                           server.authority_level_for("local_government"))

    def test_authority_inferred_from_org_conservatively(self):
        res = server.import_document({"title": "地方通知", "format": "txt",
                                      "text": "地方正文。", "organization": "广东省人民政府"})
        self.assertEqual(res["source_type"], "local_government")
        self.assertEqual(res["authority_level"], 3)
        # No org, no explicit type -> unknown (never inferred from body content).
        res2 = server.import_document({"title": "无出处", "format": "txt", "text": "无出处正文。"})
        self.assertEqual(res2["source_type"], "unknown")
        self.assertEqual(res2["authority_level"], 0)

    def test_explicit_source_type_overrides_heuristic(self):
        res = server.import_document({"title": "法条", "format": "txt", "text": "法条正文。",
                                      "organization": "广东省人民政府", "source_type": "law_regulation"})
        self.assertEqual(res["source_type"], "law_regulation")

    def test_documents_sorted_by_authority(self):
        server.import_document({"title": "低", "format": "txt", "text": "低权威。",
                                "source_type": "user_fact"})
        server.import_document({"title": "高", "format": "txt", "text": "高权威。",
                                "source_type": "law_regulation"})
        ordered = server.list_documents(sort="authority")
        self.assertEqual(ordered[0]["title"], "高")
        self.assertGreaterEqual(ordered[0]["authority_level"], ordered[-1]["authority_level"])

    def test_documents_filtered_by_source_type_and_min_authority(self):
        server.import_document({"title": "法规A", "format": "txt", "text": "法规正文。",
                                "source_type": "law_regulation"})
        server.import_document({"title": "事实B", "format": "txt", "text": "事实正文。",
                                "source_type": "user_fact"})
        laws = server.list_documents(source_type="law_regulation")
        self.assertEqual([d["title"] for d in laws], ["法规A"])
        high = server.list_documents(min_authority="4")
        self.assertEqual([d["title"] for d in high], ["法规A"])

    # --- manual version linking ----------------------------------------
    def test_manual_version_link_by_id(self):
        old = server.import_document({"title": "旧办法", "format": "txt", "text": "旧办法正文。"})
        new = server.import_document({"title": "新办法", "format": "txt", "text": "新办法正文。",
                                      "supersedes": old["document_id"]})
        self.assertEqual(new["status"], "new_version")
        self.assertEqual(new["supersedes"], old["document_id"])
        old_doc = server.get_document(old["document_id"])
        self.assertEqual(old_doc["status"], "superseded")
        self.assertEqual(old_doc["superseded_by"], new["document_id"])
        self.assertTrue(all(c["status"] == "prohibited"
                            for c in server.list_chunks(old["document_id"])))

    def test_manual_link_by_document_number(self):
        old = server.import_document({"title": "旧", "format": "txt", "text": "旧正文A。",
                                      "document_number": "粤府〔2024〕1号"})
        new = server.import_document({"title": "新", "format": "txt", "text": "新正文B。",
                                      "supersedes": "粤府〔2024〕1号"})
        self.assertEqual(new["supersedes"], old["document_id"])

    # --- incremental update by source ----------------------------------
    def test_changed_source_url_creates_new_version(self):
        old = server.import_document({"title": "政策v1", "format": "txt", "text": "版本一正文。",
                                      "source_url": "https://gov.example/policy"})
        new = server.import_document({"title": "政策v2", "format": "txt", "text": "版本二正文。",
                                      "source_url": "https://gov.example/policy"})
        self.assertEqual(new["status"], "new_version")
        self.assertEqual(new["version"], 2)
        self.assertEqual(new["supersedes"], old["document_id"])
        self.assertEqual(server.get_document(old["document_id"])["status"], "superseded")

    def test_same_content_still_duplicate(self):
        p = {"title": "同", "format": "txt", "text": "完全相同正文。",
             "source_url": "https://gov.example/dup"}
        server.import_document(dict(p))
        second = server.import_document(dict(p))
        self.assertEqual(second["status"], "duplicate")

    def test_update_document_status_prohibits_chunks(self):
        doc = server.import_document({"title": "待废止", "format": "txt", "text": "正文将被废止。"})
        self.assertTrue(all(c["status"] == "citable"
                            for c in server.list_chunks(doc["document_id"])))
        res = server.update_document({"document_id": doc["document_id"], "status": "已废止"})
        self.assertEqual(res["status"], "updated")
        self.assertEqual(server.get_document(doc["document_id"])["status"], "repealed")
        self.assertTrue(all(c["status"] == "prohibited"
                            for c in server.list_chunks(doc["document_id"])))

    # --- XLSX parse -----------------------------------------------------
    def test_xlsx_parse_rows(self):
        raw = build_xlsx([["姓名", "数量"], ["甲", "12"], ["乙", "34"]], sheet_name="表一")
        res = server.import_document({"title": "台账", "format": "xlsx",
                                      "content_base64": base64.b64encode(raw).decode()})
        self.assertEqual(res["status"], "succeeded")
        chunks = server.list_chunks(res["document_id"])
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["location_kind"], "row")
        self.assertTrue(chunks[0]["location_value"].startswith("表一!"))
        self.assertIn("姓名", chunks[0]["content"])

    def test_malformed_xlsx_quarantined(self):
        res = server.import_document({"title": "坏表.xlsx", "format": "xlsx",
                                      "content_base64": base64.b64encode(b"not a zip at all").decode()})
        self.assertEqual(res["status"], "quarantined")
        self.assertEqual(res["error_code"], "unsupported_format")

    def test_xlsx_missing_workbook_quarantined(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("random.xml", "<x/>")
        res = server.import_document({"title": "缺workbook.xlsx", "format": "xlsx",
                                      "content_base64": base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(res["status"], "quarantined")

    def test_xls_still_quarantined(self):
        res = server.import_document({"title": "老表.xls", "format": "xls",
                                      "content_base64": base64.b64encode(b"\xd0\xcf\x11\xe0").decode()})
        self.assertEqual(res["status"], "quarantined")

    # --- chunk location fields + snapshot ------------------------------
    def test_paragraph_location_and_snapshot(self):
        res = server.import_document({"title": "定位", "format": "txt",
                                      "text": "第一段。\n\n第二段。", "original_filename": "定位.txt"})
        doc = server.get_document(res["document_id"])
        self.assertEqual(doc["original_filename"], "定位.txt")
        self.assertEqual(doc["mime_type"], "text/plain")
        self.assertGreater(doc["byte_size"], 0)
        self.assertTrue(doc["raw_text"])
        chunks = server.list_chunks(res["document_id"])
        self.assertEqual(chunks[0]["location_kind"], "paragraph")
        self.assertEqual(chunks[0]["location_value"], "0")
        # Offset stability preserved from Phase 1A.
        for c in chunks:
            self.assertEqual(doc["content"][c["char_start"]:c["char_end"]], c["content"])


class EvidenceLibraryPhase2ATest(unittest.TestCase):
    """Phase 2A: deterministic retrieval, RRF, conservative claim verification."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _seed(self):
        law = server.import_document({
            "title": "Alpha Support Policy", "format": "txt",
            "text": "Alpha project shall receive 30 grants in 2026. Beta unrelated text.",
            "source_type": "law_regulation", "status": "effective", "region": "GZ",
            "document_number": "A-2026-1",
        })
        user = server.import_document({
            "title": "Alpha Field Note", "format": "txt",
            "text": "Alpha field note says 12 teams requested help in 2025.",
            "source_type": "user_fact", "status": "effective", "region": "GZ",
        })
        old = server.import_document({
            "title": "Old Alpha", "format": "txt",
            "text": "Alpha old rule mentions 99 obsolete grants.",
            "source_type": "law_regulation", "status": "repealed", "region": "GZ",
        })
        return law, user, old

    def test_search_ranks_authoritative_exact_hit(self):
        self._seed()
        res = server.search_library("Alpha project 30 grants 2026", filters={"effective_only": "true"}, limit=5)
        self.assertFalse(res["vector"]["enabled"])
        self.assertTrue(res["items"])
        top = res["items"][0]
        self.assertEqual(top["document_title"], "Alpha Support Policy")
        self.assertIn("lexical_exact", top["channels"])
        self.assertIn("fts_or_ngram", top["channels"])

    def test_search_filters_source_type_and_authority(self):
        self._seed()
        laws = server.search_library("Alpha", filters={"source_type": "law_regulation", "effective_only": "true"}, limit=10)
        self.assertTrue(laws["items"])
        self.assertTrue(all(i["source_type"] == "law_regulation" for i in laws["items"]))
        high = server.search_library("Alpha", filters={"min_authority": "4", "effective_only": "true"}, limit=10)
        self.assertTrue(all(i["authority_level"] >= 4 for i in high["items"]))

    def test_effective_only_excludes_repealed_chunks(self):
        self._seed()
        res = server.search_library("99 obsolete", filters={"effective_only": "true"}, limit=10)
        self.assertEqual(res["items"], [])

    def test_claim_supported_requires_markers_present(self):
        self._seed()
        res = server.verify_claim("Alpha project shall receive 30 grants in 2026.", filters={"effective_only": "true"})
        self.assertEqual(res["status"], "supported")
        self.assertIn("30", " ".join(res["required_markers"]))
        self.assertTrue(res["cited_chunk_ids"])

    def test_claim_missing_number_needs_verification(self):
        self._seed()
        res = server.verify_claim("Alpha project shall receive 31 grants in 2026.", filters={"effective_only": "true"})
        self.assertIn(res["status"], {"needs_verification", "unsupported"})
        self.assertIn("31", res["missing_markers"])

    def test_metric_helpers(self):
        self.assertEqual(server.recall_at_k(["a", "b"], {"b", "c"}, 2), 0.5)
        mrr = server.mean_reciprocal_rank([["x", "a"], ["b"]], [{"a"}, {"b"}])
        self.assertEqual(mrr, 0.75)

    def test_retrieval_evaluator_reports_hits_and_misses(self):
        law, _, _ = self._seed()
        chunk_id = server.list_chunks(law["document_id"])[0]["id"]
        report = server.evaluate_retrieval_cases([
            {
                "id": "exact-title",
                "query": "Alpha project 30 grants 2026",
                "filters": {"effective_only": "true"},
                "relevant_titles": ["Alpha Support Policy"],
                "relevant_chunk_ids": [chunk_id],
            },
            {
                "id": "miss",
                "query": "Delta unknown obligation",
                "filters": {"effective_only": "true"},
                "relevant_titles": ["Missing Document"],
            },
        ], k=5)
        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["miss_count"], 1)
        self.assertEqual(report["misses"][0]["id"], "miss")
        self.assertGreater(report["title_recall_at_k"], 0.0)
        self.assertGreater(report["title_mrr"], 0.0)
        self.assertGreater(report["chunk_recall_at_k"], 0.0)
        self.assertFalse(report["vector"]["enabled"])


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

    # --- Phase 1B over HTTP --------------------------------------------
    def test_http_authority_sort_and_filter(self):
        self._post("/api/library/import", {"title": "低", "format": "txt",
                                           "text": "低权威正文。", "source_type": "user_fact"})
        self._post("/api/library/import", {"title": "高", "format": "txt",
                                           "text": "高权威正文。", "source_type": "law_regulation"})
        status, docs = self._get("/api/library/documents?sort=authority")
        self.assertEqual(status, 200)
        self.assertEqual(docs["items"][0]["title"], "高")
        status, laws = self._get("/api/library/documents?source_type=law_regulation")
        self.assertEqual([d["title"] for d in laws["items"]], ["高"])
        status, high = self._get("/api/library/documents?min_authority=4")
        self.assertEqual([d["title"] for d in high["items"]], ["高"])

    def test_http_new_version_flow(self):
        status, old = self._post("/api/library/import", {
            "title": "v1", "format": "txt", "text": "第一版正文。",
            "source_url": "https://gov.example/http-ver"})
        self.assertEqual(old["status"], "succeeded")
        status, new = self._post("/api/library/import", {
            "title": "v2", "format": "txt", "text": "第二版正文。",
            "source_url": "https://gov.example/http-ver"})
        self.assertEqual(status, 201)
        self.assertEqual(new["status"], "new_version")
        self.assertEqual(new["supersedes"], old["document_id"])
        status, jobs = self._get("/api/library/jobs?status=new_version")
        self.assertEqual(len(jobs["items"]), 1)

    def test_http_xlsx_import_and_location(self):
        raw = build_xlsx([["列一", "列二"], ["甲", "乙"]], sheet_name="Sheet1")
        status, res = self._post("/api/library/import", {
            "title": "表格", "format": "xlsx",
            "content_base64": base64.b64encode(raw).decode()})
        self.assertEqual(status, 201)
        self.assertEqual(res["status"], "succeeded")
        status, chunks = self._get(f"/api/library/chunks?document_id={res['document_id']}")
        self.assertEqual(status, 200)
        self.assertTrue(all(c["location_kind"] == "row" for c in chunks["items"]))

    def test_http_update_endpoint(self):
        status, doc = self._post("/api/library/import",
                                 {"title": "改状态", "format": "txt", "text": "待更新正文。"})
        status, res = self._post("/api/library/update",
                                 {"document_id": doc["document_id"], "status": "已废止"})
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "updated")
        status, chunks = self._get(f"/api/library/chunks?document_id={doc['document_id']}")
        self.assertTrue(all(c["status"] == "prohibited" for c in chunks["items"]))


    def test_http_library_search_and_verify_claim(self):
        self._post("/api/library/import", {
            "title": "Search Policy", "format": "txt",
            "text": "Gamma policy provides 45 service windows in 2026.",
            "source_type": "law_regulation", "status": "effective", "region": "HZ",
        })
        status, search = self._get("/api/library/search?q=Gamma%2045%202026&effective_only=true&min_authority=4")
        self.assertEqual(status, 200)
        self.assertFalse(search["vector"]["enabled"])
        self.assertEqual(search["items"][0]["document_title"], "Search Policy")
        status, verify = self._post("/api/library/verify-claim", {
            "claim": "Gamma policy provides 45 service windows in 2026.",
            "filters": {"effective_only": "true"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(verify["status"], "supported")

    def test_http_retrieval_evaluation(self):
        self._post("/api/library/import", {
            "title": "Benchmark Policy", "format": "txt",
            "text": "Benchmark policy requires 18 inspections in 2026.",
            "source_type": "law_regulation", "status": "effective",
        })
        status, report = self._post("/api/library/evaluate-retrieval", {
            "k": 5,
            "cases": [{
                "id": "bench-hit",
                "query": "Benchmark 18 inspections 2026",
                "filters": {"effective_only": "true"},
                "relevant_titles": ["Benchmark Policy"],
            }],
        })
        self.assertEqual(status, 200)
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["miss_count"], 0)
        self.assertGreater(report["title_recall_at_k"], 0.0)

    def test_http_update_unknown_404(self):
        status, res = self._post("/api/library/update", {"document_id": "nope", "status": "有效"})
        self.assertEqual(status, 404)
        self.assertEqual(res["error_code"], "not_found")


if __name__ == "__main__":
    unittest.main()
