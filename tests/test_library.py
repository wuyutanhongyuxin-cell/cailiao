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

import contextlib
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


class RetrievalBM25Test(unittest.TestCase):
    """Phase 2B BM25/FTS v1: BM25-like channel, IDF weighting, tuning, tie-breaking."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _imp(self, title, text, **kw):
        payload = {"title": title, "format": "txt", "text": text, "status": "effective"}
        payload.update(kw)
        res = server.import_document(payload)
        self.assertIn(res["status"], {"succeeded", "new_version"}, f"{title} -> {res}")
        return res

    def _seed_common_and_rare(self):
        # Three high-authority docs share the common term 通知; one low-authority
        # doc additionally carries the rare term 光伏.
        self._imp("甲类通知", "甲类事项发布通知。", source_type="law_regulation")
        self._imp("乙类通知", "乙类事项发布通知。", source_type="law_regulation")
        self._imp("丙类通知", "丙类事项发布通知。", source_type="law_regulation")
        self._imp("光伏专项说明", "光伏专项事项发布通知。", source_type="user_fact")

    # --- term-space expansion ------------------------------------------
    def test_bm25_terms_cover_word_char_mixed(self):
        terms = set(server._bm25_terms("Alpha 2026 光伏项目"))
        self.assertIn("alpha", terms)      # ascii word
        self.assertIn("2026", terms)       # number token
        self.assertIn("光", terms)          # cjk unigram
        self.assertIn("光伏", terms)         # cjk bigram
        self.assertIn("光伏项目", terms)      # cjk 4-gram (within run)
        # ngram length is capped; runs longer than the cap keep sliding.
        self.assertNotIn("光伏项目补", set(server._bm25_terms("光伏项目补助")))

    # --- BM25 channel wired into search --------------------------------
    def test_bm25_channel_present_for_word_char_mixed_queries(self):
        self._imp("混合样本", "Alpha 项目 2026 光伏 补助。", source_type="law_regulation")
        for q in ("2026", "光伏", "alpha", "alpha 2026 光伏"):
            res = server.search_library(q, filters={"effective_only": "true"}, limit=10)
            self.assertTrue(res["items"], f"no items for query {q!r}")
            top = res["items"][0]
            self.assertEqual(top["document_title"], "混合样本")
            self.assertIn("bm25_like", top["channels"], f"bm25 channel missing for {q!r}")
            self.assertTrue(any(r.startswith("bm25:") for r in top["hit_reasons"]))
        # Tuning knobs are surfaced for a later sweep / harness.
        meta = res["bm25"]
        self.assertEqual(meta["k1"], server.BM25_K1)
        self.assertEqual(meta["b"], server.BM25_B)

    def test_idf_lets_rare_term_outrank_common_term(self):
        self._seed_common_and_rare()
        res = server.search_library("光伏 通知", filters={"effective_only": "true"}, limit=10)
        by_title = {i["document_title"]: i for i in res["items"]}
        self.assertIn("光伏专项说明", by_title)
        self.assertIn("甲类通知", by_title)
        rare = by_title["光伏专项说明"]
        common = by_title["甲类通知"]
        # Rare-term doc must score strictly higher on the BM25 channel thanks to IDF.
        self.assertGreater(rare["channels"]["bm25_like"]["score"],
                           common["channels"]["bm25_like"]["score"])

    def test_authority_is_tiebreaker_not_override(self):
        # The rare-term doc is the lowest authority (user_fact) yet wins on text.
        self._seed_common_and_rare()
        res = server.search_library("光伏 通知", filters={"effective_only": "true"}, limit=10)
        top = res["items"][0]
        self.assertEqual(top["document_title"], "光伏专项说明")
        self.assertEqual(top["source_type"], "user_fact")

    def test_bm25_respects_effective_only_filter(self):
        self._imp("现行通知", "现行光伏专项通知。", source_type="law_regulation", status="有效")
        self._imp("废止通知", "废止光伏专项通知。", source_type="law_regulation", status="已废止")
        res = server.search_library("光伏 通知", filters={"effective_only": "true"}, limit=10)
        titles = {i["document_title"] for i in res["items"]}
        self.assertIn("现行通知", titles)
        self.assertNotIn("废止通知", titles)

    def test_bm25_respects_min_authority_filter(self):
        # A strong BM25 text match is still excluded when it fails the authority gate.
        self._seed_common_and_rare()
        res = server.search_library("光伏", filters={"effective_only": "true", "min_authority": "4"}, limit=10)
        titles = {i["document_title"] for i in res["items"]}
        self.assertNotIn("光伏专项说明", titles)  # user_fact (authority 1) filtered out


class RetrievalEvalSuiteTest(unittest.TestCase):
    """Phase 2B: run the anonymized retrieval eval suite through evaluate_retrieval_cases.

    The suite (tests/data/retrieval_eval_suite.json) ships an anonymized synthetic
    corpus plus 10 labeled cases (8 hits + 2 intentional misses). Chunk-level
    relevance is stored as marker substrings and resolved to real chunk ids at run
    time, so the suite stays stable and reusable across runs and future rerankers.
    """

    SUITE_PATH = Path(__file__).resolve().parent / "data" / "retrieval_eval_suite.json"

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)
        with self.SUITE_PATH.open("r", encoding="utf-8") as f:
            self.suite = json.load(f)
        self._seed_corpus()

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _seed_corpus(self):
        for doc in self.suite["corpus"]:
            res = server.import_document({
                "title": doc["title"],
                "format": doc.get("format", "txt"),
                "text": doc["text"],
                "status": doc.get("status", "有效"),
                "source_type": doc.get("source_type", ""),
                "region": doc.get("region", ""),
                "document_number": doc.get("document_number", ""),
            })
            self.assertIn(res["status"], {"succeeded", "new_version"},
                          f"corpus doc failed to import: {doc['title']} -> {res}")

    def _all_chunks(self):
        chunks = []
        for d in server.list_documents():
            chunks.extend(server.list_chunks(d["id"]))
        return chunks

    def _resolve_marker(self, marker, chunks):
        """Map a marker substring to the id of the first chunk that contains it."""
        norm = server._norm_line(marker)
        for c in chunks:
            if norm in server._norm_line(c["content"]):
                return c["id"]
        return None

    def _build_cases(self):
        chunks = self._all_chunks()
        cases = []
        for case in self.suite["cases"]:
            chunk_ids = []
            for marker in case.get("relevant_chunk_markers", []):
                cid = self._resolve_marker(marker, chunks)
                self.assertIsNotNone(
                    cid, f"marker did not resolve to any chunk: {marker!r} in {case['id']}")
                chunk_ids.append(cid)
            cases.append({
                "id": case["id"],
                "query": case["query"],
                "filters": case.get("filters", {}),
                "relevant_titles": case.get("relevant_titles", []),
                "relevant_chunk_ids": chunk_ids,
            })
        return cases

    def test_suite_has_expected_shape(self):
        # Guard the fixture itself: 8-12 cases, at least 2 intentional misses.
        self.assertGreaterEqual(len(self.suite["cases"]), 8)
        self.assertLessEqual(len(self.suite["cases"]), 12)
        expected_misses = [c for c in self.suite["cases"] if c.get("expect_hit") is False]
        self.assertGreaterEqual(len(expected_misses), 2)

    def test_markers_resolve_uniquely_enough(self):
        # Every declared marker must resolve to a concrete chunk (no silent drift).
        chunks = self._all_chunks()
        for case in self.suite["cases"]:
            for marker in case.get("relevant_chunk_markers", []):
                self.assertIsNotNone(self._resolve_marker(marker, chunks),
                                     f"unresolved marker {marker!r} in {case['id']}")

    def test_suite_metrics_and_miss_reporting(self):
        cases = self._build_cases()
        report = server.evaluate_retrieval_cases(cases, k=self.suite.get("k", 10))

        self.assertEqual(report["case_count"], 10)
        self.assertEqual(report["miss_count"], 2)
        self.assertFalse(report["vector"]["enabled"])

        miss_ids = {m["id"] for m in report["misses"]}
        self.assertEqual(miss_ids, {"case-07-miss-unknown-category",
                                    "case-09-miss-repealed-filtered"})

        # 8 hits / 10 titled cases => title recall exactly 0.8; every hit's marker
        # chunk is retrieved => chunk recall 1.0 over the cases that declare chunks.
        self.assertAlmostEqual(report["title_recall_at_k"], 0.8, places=6)
        self.assertAlmostEqual(report["chunk_recall_at_k"], 1.0, places=6)
        self.assertGreater(report["title_mrr"], 0.5)
        self.assertGreater(report["chunk_mrr"], 0.5)

        by_id = {c["id"]: c for c in report["cases"]}

        # A hit case reports no missed titles and a concrete first_relevant_rank.
        hit = by_id["case-01-exact-title-and-number"]
        self.assertTrue(hit["hit"])
        self.assertEqual(hit["missed_titles"], [])
        self.assertEqual(hit["missed_chunk_ids"], [])
        self.assertIsNotNone(hit["first_relevant_rank"])

        # Explainability: top_reasons expose fused score, channels and hit reasons,
        # including the new bm25_like channel.
        self.assertTrue(hit["top_reasons"])
        first = hit["top_reasons"][0]
        self.assertEqual(first["rank"], 1)
        self.assertIn("bm25_like", first["channels"])
        self.assertIsNotNone(first["fused_score"])

        # Multi-doc recall: both labeled titles retrieved.
        multi = by_id["case-02-multi-doc-recall"]
        self.assertTrue(multi["hit"])
        self.assertEqual(multi["missed_titles"], [])
        self.assertAlmostEqual(multi["title_recall_at_k"], 1.0, places=6)

        # Miss cases name the missed title and have no first_relevant_rank.
        unknown = by_id["case-07-miss-unknown-category"]
        self.assertFalse(unknown["hit"])
        self.assertIn("庚类专项债券管理办法", unknown["missed_titles"])
        self.assertIsNone(unknown["first_relevant_rank"])

        repealed = by_id["case-09-miss-repealed-filtered"]
        self.assertFalse(repealed["hit"])
        self.assertIn("甲类项目旧办法", repealed["missed_titles"])

        # Aggregate misses carry the diagnostics, not just ids.
        agg = {m["id"]: m for m in report["misses"]}
        self.assertIn("庚类专项债券管理办法",
                      agg["case-07-miss-unknown-category"]["missed_titles"])


class RetrievalEvalHarnessTest(unittest.TestCase):
    """Phase 2B: reusable loader/runner helpers and the eval-retrieval CLI gate."""

    SUITE_PATH = Path(__file__).resolve().parent / "data" / "retrieval_eval_suite.json"

    def setUp(self):
        # A distinct temp DB as the "main" DB; the runner must not pollute it.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    # --- loader --------------------------------------------------------
    def test_loader_reads_fixture(self):
        suite = server.load_retrieval_eval_suite(self.SUITE_PATH)
        self.assertIsInstance(suite, dict)
        self.assertEqual(len(suite["cases"]), 10)
        self.assertTrue(suite["corpus"])

    def test_loader_rejects_non_object(self):
        bad = Path(self._tmp.name + ".json")
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                server.load_retrieval_eval_suite(bad)
        finally:
            bad.unlink(missing_ok=True)

    # --- runner (end-to-end) + DB isolation ----------------------------
    def test_run_suite_end_to_end_and_isolates_db(self):
        suite = server.load_retrieval_eval_suite(self.SUITE_PATH)
        report = server.run_retrieval_eval_suite(suite)
        self.assertEqual(report["case_count"], 10)
        self.assertEqual(report["miss_count"], 2)
        self.assertAlmostEqual(report["title_recall_at_k"], 0.8, places=6)
        self.assertAlmostEqual(report["chunk_recall_at_k"], 1.0, places=6)
        self.assertEqual(report["suite"], suite.get("suite"))
        self.assertIn("bm25", report)
        # DB_PATH restored and the "main" DB was never written to.
        self.assertEqual(server.DB_PATH, Path(self._tmp.name))
        self.assertEqual(server.list_documents(), [])

    def test_run_suite_honors_explicit_db_path(self):
        suite = server.load_retrieval_eval_suite(self.SUITE_PATH)
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        try:
            report = server.run_retrieval_eval_suite(suite, db_path=tmp.name)
            self.assertEqual(report["case_count"], 10)
            # Corpus persisted into the explicit DB (not removed by the runner).
            self.assertTrue(Path(tmp.name).exists())
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def test_marker_that_does_not_resolve_raises(self):
        suite = {
            "suite": "bad", "k": 5,
            "corpus": [{"title": "有正文", "text": "真实存在的正文。", "status": "有效"}],
            "cases": [{
                "id": "bad-marker", "query": "正文",
                "filters": {"effective_only": "true"},
                "relevant_titles": ["有正文"],
                "relevant_chunk_markers": ["这段标记不存在于任何分段"],
            }],
        }
        with self.assertRaises(ValueError):
            server.run_retrieval_eval_suite(suite)

    # --- CLI -----------------------------------------------------------
    def _run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = server.eval_retrieval_cli(argv)
        return code, buf.getvalue()

    def test_cli_pass_emits_json_and_exit_zero(self):
        code, out = self._run_cli([
            "--suite", str(self.SUITE_PATH), "--k", "10",
            "--min-title-recall", "0.8", "--min-chunk-recall", "1.0", "--max-misses", "2",
        ])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["case_count"], 10)
        self.assertEqual(report["mode"], "single")

    def test_cli_output_file_written(self):
        out_path = Path(self._tmp.name + ".report.json")
        try:
            code, _ = self._run_cli([
                "--suite", str(self.SUITE_PATH), "--output", str(out_path),
            ])
            self.assertEqual(code, 0)
            self.assertTrue(out_path.exists())
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["case_count"], 10)
        finally:
            out_path.unlink(missing_ok=True)

    def test_cli_threshold_failure_exits_nonzero_but_still_json(self):
        # The suite legitimately has 2 misses; max-misses 0 must fail the gate.
        code, out = self._run_cli([
            "--suite", str(self.SUITE_PATH), "--max-misses", "0",
        ])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertFalse(report["gate"]["passed"])
        self.assertTrue(report["gate"]["failures"])
        self.assertEqual(report["case_count"], 10)  # JSON still complete

    def test_cli_missing_suite_exits_two(self):
        code, out = self._run_cli(["--suite", str(self._tmp.name) + ".nope.json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_cli_sweep_reports_grid_and_best(self):
        code, out = self._run_cli([
            "--suite", str(self.SUITE_PATH), "--sweep-bm25", "--max-misses", "2",
        ])
        report = json.loads(out)
        self.assertEqual(report["mode"], "sweep")
        self.assertTrue(report["results"])
        self.assertIn("best", report)
        # Best config should still meet the regression targets on this suite.
        self.assertEqual(code, 0)
        self.assertAlmostEqual(report["best"]["title_recall_at_k"], 0.8, places=6)


class RetrievalBM25ParamTest(unittest.TestCase):
    """Phase 2B: BM25 k1/b are overridable and actually change scoring."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_default_params_reported(self):
        server.import_document({"title": "样本", "format": "txt",
                                "text": "光伏光伏光伏专项通知。", "status": "effective",
                                "source_type": "law_regulation"})
        res = server.search_library("光伏", filters={"effective_only": "true"}, limit=5)
        self.assertEqual(res["bm25"]["k1"], server.BM25_K1)
        self.assertEqual(res["bm25"]["b"], server.BM25_B)

    def test_k1_override_changes_bm25_score(self):
        # tf>1 is required for k1 to matter, so repeat the rare term.
        server.import_document({"title": "样本", "format": "txt",
                                "text": "光伏光伏光伏专项通知。", "status": "effective",
                                "source_type": "law_regulation"})
        default = server.search_library("光伏", filters={"effective_only": "true"}, limit=5)
        tuned = server.search_library("光伏", filters={"effective_only": "true"}, limit=5,
                                      bm25_params={"k1": 0.3})
        self.assertEqual(tuned["bm25"]["k1"], 0.3)
        d_score = default["items"][0]["channels"]["bm25_like"]["score"]
        t_score = tuned["items"][0]["channels"]["bm25_like"]["score"]
        self.assertNotAlmostEqual(d_score, t_score, places=6)

    def test_invalid_params_fall_back_to_defaults(self):
        server.import_document({"title": "样本", "format": "txt",
                                "text": "光伏专项通知。", "status": "effective",
                                "source_type": "law_regulation"})
        res = server.search_library("光伏", filters={"effective_only": "true"}, limit=5,
                                    bm25_params={"k1": "not-a-number", "b": 5.0})
        self.assertEqual(res["bm25"]["k1"], server.BM25_K1)
        self.assertEqual(res["bm25"]["b"], server.BM25_B)


class ClaimEvidenceMapTest(unittest.TestCase):
    """Phase 2B: claim-to-evidence mapping (deterministic lexical coverage)."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        self._orig_db_path = server.DB_PATH
        server.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        server.DB_PATH = self._orig_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def _seed_two_markers_in_two_chunks(self):
        # Two paragraphs => two chunks: one carries 30/2026, the other carries 45.
        return server.import_document({
            "title": "Alpha Policy", "format": "txt",
            "text": ("Alpha project shall receive 30 grants in 2026.\n\n"
                     "Alpha program also allocates 45 review windows."),
            "source_type": "law_regulation", "status": "effective",
        })

    def test_fully_covered_markers_map_to_chunks(self):
        self._seed_two_markers_in_two_chunks()
        res = server.verify_claim("Alpha project shall receive 30 grants in 2026.",
                                  filters={"effective_only": "true"})
        emap = res["evidence_map"]
        self.assertIn("30", emap["required_markers"])
        self.assertIn("2026", emap["required_markers"])
        self.assertEqual(emap["missing_markers"], [])
        # Every required marker attributed to a non-empty list of chunk ids.
        for m in ("30", "2026"):
            self.assertIn(m, emap["covered_markers"])
            self.assertIsInstance(emap["covered_markers"][m], list)
            self.assertTrue(emap["covered_markers"][m])
        self.assertAlmostEqual(emap["coverage_ratio"], 1.0, places=6)
        self.assertEqual(res["status"], "supported")
        # cited chunk ids prefer a marker-matching chunk.
        self.assertTrue(res["cited_chunk_ids"])
        self.assertIn(res["cited_chunk_ids"][0], emap["covered_markers"]["30"])

    def test_missing_marker_is_not_supported(self):
        self._seed_two_markers_in_two_chunks()
        res = server.verify_claim("Alpha project shall receive 31 grants in 2026.",
                                  filters={"effective_only": "true"})
        emap = res["evidence_map"]
        self.assertIn("31", emap["missing_markers"])
        self.assertNotIn("31", emap["covered_markers"])
        self.assertLess(emap["coverage_ratio"], 1.0)
        self.assertIn(res["status"], {"needs_verification", "unsupported"})

    def test_multiple_chunks_each_cover_some_markers(self):
        self._seed_two_markers_in_two_chunks()
        res = server.verify_claim("Alpha 30 grants 2026 and 45 windows.",
                                  filters={"effective_only": "true"})
        emap = res["evidence_map"]
        # The 45 marker and the 30/2026 markers live in different chunks; each
        # covered_markers value is a list of covering chunk ids.
        chunk_for_30 = emap["covered_markers"].get("30", [])[0]
        chunk_for_45 = emap["covered_markers"].get("45", [])[0]
        self.assertTrue(chunk_for_30)
        self.assertTrue(chunk_for_45)
        self.assertNotEqual(chunk_for_30, chunk_for_45)
        # supporting_items expose each chunk's matched markers.
        by_chunk = {it["chunk_id"]: it for it in emap["supporting_items"]}
        self.assertIn("30", by_chunk[chunk_for_30]["matched_markers"])
        self.assertIn("45", by_chunk[chunk_for_45]["matched_markers"])

    def test_marker_in_multiple_chunks_lists_all(self):
        # The same marker (2025) appears in two separate documents/chunks; the
        # covered_markers entry must list both chunk ids in ranked order.
        server.import_document({
            "title": "Plan A", "format": "txt",
            "text": "Program A targets 2025 milestones for delta review.",
            "source_type": "law_regulation", "status": "effective",
        })
        server.import_document({
            "title": "Plan B", "format": "txt",
            "text": "Program B also cites 2025 delta obligations.",
            "source_type": "law_regulation", "status": "effective",
        })
        res = server.verify_claim("delta 2025 program obligations",
                                  filters={"effective_only": "true"})
        emap = res["evidence_map"]
        ids = emap["covered_markers"].get("2025", [])
        self.assertGreaterEqual(len(ids), 2)
        self.assertEqual(len(ids), len(set(ids)))  # deduped
        # order matches the retrieval ranking of the supporting chunks
        ranked_chunk_order = [it["chunk_id"] for it in emap["supporting_items"]
                              if "2025" in it["matched_markers"]]
        self.assertEqual(ids, ranked_chunk_order)

    def test_pure_text_claim_uses_matched_terms(self):
        server.import_document({
            "title": "Policy Note", "format": "txt",
            "text": "Alpha program improves coordination and reporting.",
            "source_type": "law_regulation", "status": "effective",
        })
        res = server.verify_claim("Alpha program coordination",
                                  filters={"effective_only": "true"})
        emap = res["evidence_map"]
        self.assertEqual(emap["required_markers"], [])
        self.assertIsNone(emap["coverage_ratio"])
        self.assertTrue(emap["supporting_items"])
        self.assertTrue(emap["supporting_items"][0]["matched_terms"])
        # No numeric/policy markers => never "supported" on lexical terms alone.
        self.assertNotEqual(res["status"], "supported")

    def test_map_helper_direct_no_items(self):
        emap = server.map_claim_to_evidence("Alpha 30 grants 2026.", [])
        self.assertEqual(emap["supporting_items"], [])
        self.assertEqual(emap["covered_markers"], {})
        self.assertEqual(set(emap["missing_markers"]), {"30", "2026"})
        self.assertAlmostEqual(emap["coverage_ratio"], 0.0, places=6)


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
        # Phase 2B: the claim-to-evidence map is returned over HTTP.
        emap = verify["evidence_map"]
        self.assertIn("45", emap["covered_markers"])
        self.assertIn("2026", emap["covered_markers"])
        # covered_markers values survive JSON round-trip as lists of chunk ids.
        self.assertIsInstance(emap["covered_markers"]["45"], list)
        self.assertTrue(emap["covered_markers"]["45"])
        self.assertEqual(emap["missing_markers"], [])
        self.assertTrue(emap["supporting_items"])
        self.assertTrue(emap["supporting_items"][0]["matched_markers"])

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

    def test_http_retrieval_evaluation_reports_misses(self):
        self._post("/api/library/import", {
            "title": "Benchmark Policy", "format": "txt",
            "text": "Benchmark policy requires 18 inspections in 2026.",
            "source_type": "law_regulation", "status": "effective",
        })
        status, report = self._post("/api/library/evaluate-retrieval", {
            "k": 5,
            "cases": [
                {
                    "id": "bench-hit",
                    "query": "Benchmark 18 inspections 2026",
                    "filters": {"effective_only": "true"},
                    "relevant_titles": ["Benchmark Policy"],
                },
                {
                    "id": "bench-miss",
                    "query": "Nonexistent obligation elsewhere",
                    "filters": {"effective_only": "true"},
                    "relevant_titles": ["Absent Document"],
                },
            ],
        })
        self.assertEqual(status, 200)
        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["miss_count"], 1)
        self.assertEqual(report["misses"][0]["id"], "bench-miss")
        self.assertIn("Absent Document", report["misses"][0]["missed_titles"])
        by_id = {c["id"]: c for c in report["cases"]}
        self.assertEqual(by_id["bench-hit"]["missed_titles"], [])
        self.assertIsNotNone(by_id["bench-hit"]["first_relevant_rank"])

    def test_http_update_unknown_404(self):
        status, res = self._post("/api/library/update", {"document_id": "nope", "status": "有效"})
        self.assertEqual(status, 404)
        self.assertEqual(res["error_code"], "not_found")


if __name__ == "__main__":
    unittest.main()
