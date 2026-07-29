from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
RULES_PATH = ROOT / "rules" / "material_rules.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "materials.sqlite3"

with RULES_PATH.open("r", encoding="utf-8-sig") as f:
    RULES = json.load(f)

DATA_DIR.mkdir(exist_ok=True)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    init_schema(conn)
    return conn


# --- Phase 1: trusted evidence library ---------------------------------------

# Document validity / status values (accept Chinese aliases, normalize to these).
DOC_STATUS_VALUES = {"effective", "revised", "repealed", "expired", "superseded", "draft", "unknown"}
DOC_STATUS_ALIASES = {
    "有效": "effective", "现行有效": "effective", "现行": "effective",
    "已修订": "revised", "修订": "revised",
    "已废止": "repealed", "废止": "repealed",
    "已失效": "expired", "失效": "expired",
    "已被取代": "superseded", "被取代": "superseded", "取代": "superseded", "已替代": "superseded",
    "征求意见": "draft", "草案": "draft",
    "未知": "unknown", "": "unknown",
}
# Statuses that make a document non-recommendable by default (chunks -> prohibited).
NON_CITABLE_STATUS = {"repealed", "expired", "superseded"}

CHUNK_STATUS_VALUES = {"citable", "reference_only", "prohibited"}

# Import job lifecycle states.
JOB_STATUS_VALUES = {"succeeded", "duplicate", "new_version", "updated", "failed", "quarantined"}

# Authority ranking. source_type is normalized conservatively; authority_level is
# derived from source_type only (never inferred from document content).
SOURCE_TYPE_VALUES = {
    "law_regulation", "state_council", "ministry", "local_government",
    "official_media", "user_fact", "unknown",
}
SOURCE_TYPE_ALIASES = {
    "法律法规": "law_regulation", "法律": "law_regulation", "法规": "law_regulation",
    "行政法规": "law_regulation", "宪法": "law_regulation", "law": "law_regulation",
    "regulation": "law_regulation",
    "国务院": "state_council", "国办": "state_council", "国务院办公厅": "state_council",
    "部委": "ministry", "部门规章": "ministry", "部委文件": "ministry", "ministry": "ministry",
    "地方政府": "local_government", "地方": "local_government", "省政府": "local_government",
    "市政府": "local_government", "县政府": "local_government",
    "权威媒体": "official_media", "官方媒体": "official_media", "媒体": "official_media",
    "media": "official_media",
    "用户事实": "user_fact", "内部事实": "user_fact", "用户": "user_fact", "内部资料": "user_fact",
    "未知": "unknown", "": "unknown",
}
AUTHORITY_LEVEL = {
    "law_regulation": 6,
    "state_council": 5,
    "ministry": 4,
    "local_government": 3,
    "official_media": 2,
    "user_fact": 1,
    "unknown": 0,
}
# Conservative organization-name heuristics for source_type inference. Order matters
# (first match wins); only applied when payload does not supply an explicit type.
ORG_SOURCE_TYPE_HEURISTICS = [
    ("state_council", ("国务院办公厅", "国务院办", "国务院")),
    ("ministry", ("部", "委员会", "总局", "总署", "国家局", "银保监", "证监", "海关总署")),
    ("local_government", ("省人民政府", "市人民政府", "县人民政府", "省政府", "市政府",
                          "县政府", "自治区", "街道办", "区人民政府")),
    ("official_media", ("人民日报", "新华社", "新华网", "央视", "中国政府网", "光明日报", "经济日报")),
]

SUPPORTED_FORMATS = {"txt", "html", "htm", "docx", "xlsx"}
# Formats we knowingly cannot parse in this phase and must not silently accept.
QUARANTINE_FORMATS = {"pdf", "xls", "doc", "ppt", "pptx"}

# location_kind values for chunks.
LOCATION_PARAGRAPH = "paragraph"
LOCATION_ROW = "row"

MIME_BY_FORMAT = {
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


class ImportError_(Exception):
    """Base class for evidence-library import failures."""

    reason_code = "import_error"


class ParseError(ImportError_):
    reason_code = "parse_error"


class QuarantineError(ImportError_):
    """Raised for formats we refuse to parse (e.g. PDF) so nothing is silently accepted."""

    reason_code = "unsupported_format"


# Full column list for documents (base Phase 1A + Phase 1B additions), used by
# named-column INSERT/SELECT so schema growth stays robust.
DOC_COLUMNS = [
    "id", "title", "source_url", "organization", "document_number", "publish_date",
    "status", "format", "sha256", "char_count", "content", "imported_at",
    # Phase 1B additions:
    "source_type", "authority_level", "region", "jurisdiction",
    "valid_from", "valid_to", "supersedes", "superseded_by", "related_document_id",
    "version", "version_note", "original_filename", "mime_type", "byte_size", "raw_text",
]
# Columns returned in list views (exclude large content/raw_text).
DOC_LIST_COLUMNS = [c for c in DOC_COLUMNS if c not in ("content", "raw_text")]

CHUNK_COLUMNS = [
    "id", "document_id", "chunk_index", "char_start", "char_end", "status", "content",
    "location_kind", "location_value",
]

JOB_COLUMNS = [
    "id", "title", "source_url", "format", "status", "document_id", "sha256",
    "error_code", "error_reason", "quarantined", "created_at",
    "related_document_id", "note",
]

# Phase 1B columns to add to pre-existing tables via idempotent migration.
_DOC_MIGRATION_COLUMNS = {
    "source_type": "TEXT", "authority_level": "INTEGER", "region": "TEXT",
    "jurisdiction": "TEXT", "valid_from": "TEXT", "valid_to": "TEXT",
    "supersedes": "TEXT", "superseded_by": "TEXT", "related_document_id": "TEXT",
    "version": "INTEGER", "version_note": "TEXT", "original_filename": "TEXT",
    "mime_type": "TEXT", "byte_size": "INTEGER", "raw_text": "TEXT",
}
_CHUNK_MIGRATION_COLUMNS = {"location_kind": "TEXT", "location_value": "TEXT"}
_JOB_MIGRATION_COLUMNS = {"related_document_id": "TEXT", "note": "TEXT"}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Idempotently add missing columns to an existing table (simple migration)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, coltype in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create every table if missing. Safe to call repeatedly (idempotent)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, title TEXT, source TEXT, url TEXT, body TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(id UNINDEXED, title, source, body)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT,
            source_url TEXT,
            organization TEXT,
            document_number TEXT,
            publish_date TEXT,
            status TEXT,
            format TEXT,
            sha256 TEXT UNIQUE,
            char_count INTEGER,
            content TEXT,
            imported_at TEXT,
            source_type TEXT,
            authority_level INTEGER,
            region TEXT,
            jurisdiction TEXT,
            valid_from TEXT,
            valid_to TEXT,
            supersedes TEXT,
            superseded_by TEXT,
            related_document_id TEXT,
            version INTEGER,
            version_note TEXT,
            original_filename TEXT,
            mime_type TEXT,
            byte_size INTEGER,
            raw_text TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            char_start INTEGER,
            char_end INTEGER,
            status TEXT,
            content TEXT,
            location_kind TEXT,
            location_value TEXT,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            source_url TEXT,
            format TEXT,
            status TEXT,
            document_id TEXT,
            sha256 TEXT,
            error_code TEXT,
            error_reason TEXT,
            quarantined INTEGER DEFAULT 0,
            created_at TEXT,
            related_document_id TEXT,
            note TEXT
        )
        """
    )
    # Migrate tables created by an earlier (Phase 1A) schema.
    _ensure_columns(conn, "documents", _DOC_MIGRATION_COLUMNS)
    _ensure_columns(conn, "evidence_chunks", _CHUNK_MIGRATION_COLUMNS)
    _ensure_columns(conn, "import_jobs", _JOB_MIGRATION_COLUMNS)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON evidence_chunks(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON import_jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_source_url ON documents(source_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_docnum ON documents(document_number)")
    conn.commit()


def _insert_row(conn: sqlite3.Connection, table: str, columns: list[str], values: dict[str, Any]) -> None:
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(values.get(c) for c in columns),
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_status(value: str) -> str:
    """Map a free-form validity/status string to a canonical DOC_STATUS value."""
    raw = (value or "").strip()
    low = raw.lower()
    if low in DOC_STATUS_VALUES:
        return low
    if raw in DOC_STATUS_ALIASES:
        return DOC_STATUS_ALIASES[raw]
    return "unknown"


def normalize_source_type(value: str) -> str:
    """Map a free-form source_type string to a canonical SOURCE_TYPE value."""
    raw = (value or "").strip()
    low = raw.lower()
    if low in SOURCE_TYPE_VALUES:
        return low
    if raw in SOURCE_TYPE_ALIASES:
        return SOURCE_TYPE_ALIASES[raw]
    if low in SOURCE_TYPE_ALIASES:
        return SOURCE_TYPE_ALIASES[low]
    return "unknown"


def infer_source_type(explicit: str, organization: str) -> str:
    """Determine source_type conservatively.

    Priority: explicit payload value -> organization-name heuristics -> unknown.
    Never inferred from document body content.
    """
    normalized = normalize_source_type(explicit)
    if normalized != "unknown":
        return normalized
    org = (organization or "").strip()
    if org:
        for source_type, needles in ORG_SOURCE_TYPE_HEURISTICS:
            if any(n in org for n in needles):
                return source_type
    return "unknown"


def authority_level_for(source_type: str) -> int:
    return AUTHORITY_LEVEL.get(source_type, 0)


class _TextHTMLParser(HTMLParser):
    """Collect visible text, dropping <script>/<style> and collapsing block tags to newlines."""

    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# A "segment" is one located unit of source text: {"text", "location_kind", "location_value"}.

def _norm_line(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def _paragraphs_from_text(text: str) -> list[str]:
    """Split plain/HTML text into paragraphs on blank lines."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def extract_txt_segments(raw: bytes) -> list[dict[str, Any]]:
    paras = _paragraphs_from_text(_decode_bytes(raw))
    return [{"text": p, "location_kind": LOCATION_PARAGRAPH, "location_value": str(i)}
            for i, p in enumerate(paras)]


def extract_html_segments(raw: bytes) -> list[dict[str, Any]]:
    parser = _TextHTMLParser()
    try:
        parser.feed(_decode_bytes(raw))
    except Exception as exc:  # pragma: no cover - defensive
        raise ParseError(f"HTML 解析失败：{exc}") from exc
    paras = _paragraphs_from_text(parser.text())
    return [{"text": p, "location_kind": LOCATION_PARAGRAPH, "location_value": str(i)}
            for i, p in enumerate(paras)]


def extract_docx_segments(raw: bytes) -> list[dict[str, Any]]:
    """Extract paragraph text from a DOCX (Office Open XML) archive using stdlib only."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ParseError(f"DOCX 结构无效或缺少 word/document.xml：{exc}") from exc
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ParseError(f"DOCX XML 解析失败：{exc}") from exc
    segments: list[dict[str, Any]] = []
    idx = 0
    for para in root.iter(f"{ns}p"):
        texts = [node.text or "" for node in para.iter(f"{ns}t")]
        line = "".join(texts).strip()
        if line:
            segments.append({"text": line, "location_kind": LOCATION_PARAGRAPH,
                             "location_value": str(idx)})
            idx += 1
    return segments


def _col_to_index(cell_ref: str) -> int:
    """Convert an A1-style cell reference's column letters to a 0-based index."""
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
    return n - 1 if n > 0 else 0


def extract_xlsx_segments(raw: bytes) -> list[dict[str, Any]]:
    """Parse .xlsx (OOXML zip) with stdlib only: sharedStrings + worksheets -> row segments.

    Malformed archives or missing workbook parts are quarantined with a clear error.
    """
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise QuarantineError(f"XLSX 不是有效的 zip/OOXML 结构，已隔离：{exc}") from exc
    with zf:
        names = set(zf.namelist())
        if "xl/workbook.xml" not in names:
            raise QuarantineError("XLSX 缺少 xl/workbook.xml，工作簿结构不受支持，已隔离。")
        # Shared strings (optional).
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            try:
                sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            except ET.ParseError as exc:
                raise QuarantineError(f"XLSX sharedStrings.xml 解析失败，已隔离：{exc}") from exc
            for si in sroot.iter(f"{main_ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{main_ns}t")))
        # Sheet name -> r:id, and r:id -> target, to map worksheets to display names.
        try:
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        except ET.ParseError as exc:
            raise QuarantineError(f"XLSX workbook.xml 解析失败，已隔离：{exc}") from exc
        rid_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        sheets = []  # (name, rid)
        for sh in wb_root.iter(f"{main_ns}sheet"):
            sheets.append((sh.get("name") or f"Sheet{len(sheets)+1}", sh.get(f"{rid_ns}id")))
        rid_to_target = {}
        if "xl/_rels/workbook.xml.rels" in names:
            try:
                rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            except ET.ParseError:
                rels_root = None
            if rels_root is not None:
                for rel in rels_root.iter(f"{rel_ns}Relationship"):
                    rid_to_target[rel.get("Id")] = rel.get("Target")

        def resolve_sheet_path(rid: str, ordinal: int) -> str:
            target = rid_to_target.get(rid, "")
            if target:
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
                if target in names:
                    return target
            fallback = f"xl/worksheets/sheet{ordinal}.xml"
            return fallback if fallback in names else ""

        segments: list[dict[str, Any]] = []
        for ordinal, (sheet_name, rid) in enumerate(sheets, start=1):
            path = resolve_sheet_path(rid, ordinal)
            if not path or path not in names:
                continue
            try:
                ws_root = ET.fromstring(zf.read(path))
            except ET.ParseError as exc:
                raise QuarantineError(f"XLSX 工作表 {sheet_name} 解析失败，已隔离：{exc}") from exc
            for row in ws_root.iter(f"{main_ns}row"):
                row_num = row.get("r") or ""
                cells: list[str] = []
                for c in row.iter(f"{main_ns}c"):
                    ctype = c.get("t")
                    v = c.find(f"{main_ns}v")
                    text_val = ""
                    if ctype == "s":  # shared string index
                        if v is not None and (v.text or "").strip().isdigit():
                            si = int(v.text.strip())
                            if 0 <= si < len(shared):
                                text_val = shared[si]
                    elif ctype == "inlineStr":
                        is_el = c.find(f"{main_ns}is")
                        if is_el is not None:
                            text_val = "".join(t.text or "" for t in is_el.iter(f"{main_ns}t"))
                    else:
                        if v is not None:
                            text_val = v.text or ""
                    if text_val.strip():
                        cells.append(text_val.strip())
                if cells:
                    segments.append({
                        "text": " | ".join(cells),
                        "location_kind": LOCATION_ROW,
                        "location_value": f"{sheet_name}!{row_num}" if row_num else sheet_name,
                    })
        return segments


def extract_segments(fmt: str, raw: bytes) -> list[dict[str, Any]]:
    """Dispatch to a format-specific segment extractor; quarantine unsupported."""
    fmt = (fmt or "").lower().lstrip(".")
    if fmt in QUARANTINE_FORMATS:
        raise QuarantineError(f"{fmt.upper()} 暂不支持解析，已隔离，需转换为 TXT/HTML/DOCX/XLSX 后重试。")
    if fmt == "txt":
        segments = extract_txt_segments(raw)
    elif fmt in ("html", "htm"):
        segments = extract_html_segments(raw)
    elif fmt == "docx":
        segments = extract_docx_segments(raw)
    elif fmt == "xlsx":
        segments = extract_xlsx_segments(raw)
    else:
        raise QuarantineError(f"未知格式“{fmt}”，已隔离，未静默入库。")
    # Normalize each segment's text; drop empties.
    cleaned = []
    for seg in segments:
        norm = _norm_line(seg["text"])
        if norm:
            cleaned.append({**seg, "text": norm})
    if not cleaned:
        raise ParseError("解析后正文为空，可能是空文件或不受支持的内部结构。")
    return cleaned


def build_content(segments: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Join segments into normalized content and record each segment's char span.

    Segments are joined with a blank line so offsets are stable by construction:
    content[span_start:span_end] == segment text.
    """
    parts: list[str] = []
    spans: list[dict[str, Any]] = []
    pos = 0
    for seg in segments:
        text = seg["text"]
        if parts:
            parts.append("\n\n")
            pos += 2
        parts.append(text)
        spans.append({"start": pos, "end": pos + len(text),
                      "location_kind": seg["location_kind"],
                      "location_value": seg["location_value"]})
        pos += len(text)
    return "".join(parts), spans


CHUNK_MAX_CHARS = 600


def chunk_segments(spans: list[dict[str, Any]], content: str, base_status: str) -> list[dict[str, Any]]:
    """Build chunks from segment spans, preserving location and stable char offsets.

    Long segments are sub-split while keeping offsets into `content`.
    """
    chunks: list[dict[str, Any]] = []
    index = 0
    for span in spans:
        seg_text = content[span["start"]:span["end"]]
        pos = 0
        while pos < len(seg_text):
            piece = seg_text[pos:pos + CHUNK_MAX_CHARS]
            start = span["start"] + pos
            chunks.append({
                "chunk_index": index,
                "char_start": start,
                "char_end": start + len(piece),
                "status": base_status,
                "content": piece,
                "location_kind": span["location_kind"],
                "location_value": span["location_value"],
            })
            index += 1
            pos += CHUNK_MAX_CHARS
    return chunks


def default_chunk_status(doc_status: str) -> str:
    """Repealed/expired/superseded documents default their chunks to prohibited."""
    return "prohibited" if doc_status in NON_CITABLE_STATUS else "citable"


def _decode_import_payload(payload: dict[str, Any]) -> tuple[str, bytes]:
    """Return (format, raw_bytes) from an import payload (text or base64 content)."""
    fmt = str(payload.get("format", "")).lower().lstrip(".").strip()
    if payload.get("content_base64"):
        raw = base64.b64decode(payload["content_base64"])
        if not fmt:
            fmt = "docx"
    else:
        text = payload.get("text", payload.get("content", "")) or ""
        raw = str(text).encode("utf-8")
        if not fmt:
            fmt = "txt"
    return fmt, raw


def resolve_document_ref(conn: sqlite3.Connection, ref: str) -> str | None:
    """Resolve a document reference to an id: try id, then document_number, then source_url.

    Returns the id only when the match is unambiguous (exactly one row); else None.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    row = conn.execute("SELECT id FROM documents WHERE id=?", (ref,)).fetchone()
    if row:
        return row[0]
    for col in ("document_number", "source_url"):
        rows = conn.execute(f"SELECT id FROM documents WHERE {col}=?", (ref,)).fetchall()
        if len(rows) == 1:
            return rows[0][0]
    return None


def _find_current_head(conn: sqlite3.Connection, source_url: str, document_number: str) -> str | None:
    """Find the current (non-superseded) head document matching source_url or
    document_number, but only when the match is unambiguous."""
    for col, val in (("source_url", source_url), ("document_number", document_number)):
        if not val:
            continue
        rows = conn.execute(
            f"SELECT id FROM documents WHERE {col}=? AND (superseded_by IS NULL OR superseded_by='')",
            (val,),
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
    return None


def _mark_superseded(conn: sqlite3.Connection, old_id: str, new_id: str) -> None:
    """Point old_id at its successor and prohibit its chunks."""
    conn.execute(
        "UPDATE documents SET superseded_by=?, status='superseded' WHERE id=?",
        (new_id, old_id),
    )
    conn.execute("UPDATE evidence_chunks SET status='prohibited' WHERE document_id=?", (old_id,))


def import_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Import one document. Always records an import_job; failures are queryable."""
    conn = db()
    try:
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat(timespec="seconds")
        title = str(payload.get("title", "")).strip() or "未命名文件"
        source_url = str(payload.get("source_url", payload.get("url", ""))).strip()
        organization = str(payload.get("organization", "")).strip()
        document_number = str(payload.get("document_number", "")).strip()
        publish_date = str(payload.get("publish_date", "")).strip()
        doc_status = normalize_status(str(payload.get("status", payload.get("validity", ""))))
        source_type = infer_source_type(str(payload.get("source_type", "")), organization)
        authority = authority_level_for(source_type)
        region = str(payload.get("region", "")).strip()
        jurisdiction = str(payload.get("jurisdiction", "")).strip()
        valid_from = str(payload.get("valid_from", "")).strip()
        valid_to = str(payload.get("valid_to", "")).strip()
        version_note = str(payload.get("version_note", "")).strip()
        original_filename = str(payload.get("original_filename", "")).strip()
        override_chunk_status = payload.get("chunk_status")
        if override_chunk_status not in CHUNK_STATUS_VALUES:
            override_chunk_status = None

        try:
            fmt, raw = _decode_import_payload(payload)
        except Exception as exc:
            return _record_failed_job(conn, job_id, title, source_url, "", now,
                                      ParseError(f"内容解码失败：{exc}"))

        try:
            segments = extract_segments(fmt, raw)
        except QuarantineError as exc:
            return _record_failed_job(conn, job_id, title, source_url, fmt, now, exc, quarantined=True)
        except ImportError_ as exc:
            return _record_failed_job(conn, job_id, title, source_url, fmt, now, exc)

        content, spans = build_content(segments)
        digest = sha256_hex(content)

        # Same content anywhere => duplicate (Phase 1A behavior preserved).
        existing = conn.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
        if existing:
            return _record_duplicate_job(conn, job_id, title, source_url, fmt, digest, now, existing[0])

        # Explicit manual version link, if provided; else unambiguous same-source head.
        explicit_prev_ref = str(payload.get("supersedes", "")).strip()
        prev_id = resolve_document_ref(conn, explicit_prev_ref) if explicit_prev_ref else None
        is_new_version = prev_id is not None
        if prev_id is None:
            prev_id = _find_current_head(conn, source_url, document_number)
            is_new_version = prev_id is not None

        version = 1
        if prev_id:
            prev_ver = conn.execute("SELECT version FROM documents WHERE id=?", (prev_id,)).fetchone()
            version = (prev_ver[0] or 1) + 1 if prev_ver else 2

        related_ref = str(payload.get("related_document_id", "")).strip()
        related_id = resolve_document_ref(conn, related_ref) if related_ref else None

        doc_id = str(uuid.uuid4())
        chunk_status = override_chunk_status or default_chunk_status(doc_status)
        chunks = chunk_segments(spans, content, chunk_status)
        raw_text = content if len(content) <= 200_000 else content[:200_000]
        row = {
            "id": doc_id, "title": title, "source_url": source_url,
            "organization": organization, "document_number": document_number,
            "publish_date": publish_date, "status": doc_status, "format": fmt,
            "sha256": digest, "char_count": len(content), "content": content,
            "imported_at": now, "source_type": source_type, "authority_level": authority,
            "region": region, "jurisdiction": jurisdiction, "valid_from": valid_from,
            "valid_to": valid_to, "supersedes": prev_id, "superseded_by": None,
            "related_document_id": related_id, "version": version,
            "version_note": version_note, "original_filename": original_filename,
            "mime_type": MIME_BY_FORMAT.get(fmt, "application/octet-stream"),
            "byte_size": len(raw), "raw_text": raw_text,
        }
        try:
            _insert_row(conn, "documents", DOC_COLUMNS, row)
        except sqlite3.IntegrityError:
            conn.rollback()
            r = conn.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
            return _record_duplicate_job(conn, job_id, title, source_url, fmt, digest, now,
                                         r[0] if r else None)
        for ch in chunks:
            _insert_row(conn, "evidence_chunks", CHUNK_COLUMNS, {
                "id": str(uuid.uuid4()), "document_id": doc_id, **ch,
            })
        if prev_id:
            _mark_superseded(conn, prev_id, doc_id)

        status = "new_version" if is_new_version else "succeeded"
        note = f"新版本，取代文档 {prev_id}" if is_new_version else None
        _insert_row(conn, "import_jobs", JOB_COLUMNS, {
            "id": job_id, "title": title, "source_url": source_url, "format": fmt,
            "status": status, "document_id": doc_id, "sha256": digest,
            "error_code": None, "error_reason": None, "quarantined": 0,
            "created_at": now, "related_document_id": prev_id, "note": note,
        })
        conn.commit()
        return {"status": status, "job_id": job_id, "document_id": doc_id,
                "sha256": digest, "format": fmt, "doc_status": doc_status,
                "source_type": source_type, "authority_level": authority,
                "version": version, "supersedes": prev_id,
                "chunk_count": len(chunks), "char_count": len(content)}
    finally:
        conn.close()


def update_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Manually update metadata / version relationships of an existing document.

    Does not re-parse content. Records an 'updated' import_job. When status becomes
    non-citable, the document's chunks are set to prohibited.
    """
    conn = db()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        ref = str(payload.get("document_id", payload.get("id", ""))).strip()
        doc_id = resolve_document_ref(conn, ref)
        if not doc_id:
            return {"status": "error", "error_code": "not_found",
                    "error_reason": f"未找到文档：{ref}"}

        updates: dict[str, Any] = {}
        # Simple scalar metadata fields.
        for field in ("title", "region", "jurisdiction", "valid_from", "valid_to",
                      "version_note", "document_number", "source_url"):
            if field in payload:
                updates[field] = str(payload.get(field, "")).strip()
        if "status" in payload or "validity" in payload:
            updates["status"] = normalize_status(str(payload.get("status", payload.get("validity", ""))))
        if "source_type" in payload:
            st = normalize_source_type(str(payload.get("source_type", "")))
            updates["source_type"] = st
            updates["authority_level"] = authority_level_for(st)

        # Relationship linking by reference.
        for field in ("supersedes", "superseded_by", "related_document_id"):
            if field in payload:
                ref_val = str(payload.get(field, "")).strip()
                updates[field] = resolve_document_ref(conn, ref_val) if ref_val else None

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE documents SET {set_clause} WHERE id=?",
                         (*updates.values(), doc_id))

        # Keep the other side of an explicit supersedes link consistent.
        if updates.get("supersedes"):
            conn.execute("UPDATE documents SET superseded_by=? WHERE id=?",
                         (doc_id, updates["supersedes"]))
            conn.execute("UPDATE evidence_chunks SET status='prohibited' WHERE document_id=?",
                         (updates["supersedes"],))

        # Cascade chunk prohibition when the document is now non-citable.
        cur_status = conn.execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()[0]
        if cur_status in NON_CITABLE_STATUS:
            conn.execute("UPDATE evidence_chunks SET status='prohibited' WHERE document_id=?", (doc_id,))

        job_id = str(uuid.uuid4())
        _insert_row(conn, "import_jobs", JOB_COLUMNS, {
            "id": job_id, "title": updates.get("title", ""), "source_url": "",
            "format": "", "status": "updated", "document_id": doc_id, "sha256": None,
            "error_code": None, "error_reason": None, "quarantined": 0,
            "created_at": now, "related_document_id": updates.get("supersedes"),
            "note": "manual metadata/version update",
        })
        conn.commit()
        return {"status": "updated", "job_id": job_id, "document_id": doc_id,
                "updated_fields": sorted(updates.keys()), "doc_status": cur_status}
    finally:
        conn.close()


def _record_duplicate_job(conn: sqlite3.Connection, job_id: str, title: str, source_url: str,
                          fmt: str, digest: str, now: str, existing_id: str | None) -> dict[str, Any]:
    _insert_row(conn, "import_jobs", JOB_COLUMNS, {
        "id": job_id, "title": title, "source_url": source_url, "format": fmt,
        "status": "duplicate", "document_id": existing_id, "sha256": digest,
        "error_code": "duplicate", "error_reason": "内容 SHA256 与已有文档一致，未重复入库。",
        "quarantined": 0, "created_at": now, "related_document_id": None, "note": None,
    })
    conn.commit()
    return {"status": "duplicate", "job_id": job_id, "document_id": existing_id,
            "sha256": digest, "message": "重复内容，已跳过入库。"}


def _record_failed_job(conn: sqlite3.Connection, job_id: str, title: str, source_url: str,
                       fmt: str, now: str, exc: ImportError_, quarantined: bool = False) -> dict[str, Any]:
    status = "quarantined" if quarantined else "failed"
    reason = str(exc)
    _insert_row(conn, "import_jobs", JOB_COLUMNS, {
        "id": job_id, "title": title, "source_url": source_url, "format": fmt,
        "status": status, "document_id": None, "sha256": None,
        "error_code": exc.reason_code, "error_reason": reason,
        "quarantined": 1 if quarantined else 0, "created_at": now,
        "related_document_id": None, "note": None,
    })
    conn.commit()
    return {"status": status, "job_id": job_id, "error_code": exc.reason_code,
            "error_reason": reason, "quarantined": quarantined}


def list_documents(source_type: str = "", region: str = "", min_authority: str = "",
                   sort: str = "") -> list[dict[str, Any]]:
    """List documents with optional authority/region filtering and sorting."""
    conn = db()
    cols = DOC_LIST_COLUMNS
    where: list[str] = []
    params: list[Any] = []
    if source_type:
        where.append("source_type=?")
        params.append(normalize_source_type(source_type))
    if region:
        where.append("region=?")
        params.append(region)
    if str(min_authority).strip():
        try:
            where.append("authority_level>=?")
            params.append(int(min_authority))
        except (TypeError, ValueError):
            pass
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    if sort == "authority":
        order = "ORDER BY authority_level DESC, imported_at DESC"
    else:
        order = "ORDER BY imported_at DESC"
    rows = conn.execute(f"SELECT {','.join(cols)} FROM documents{where_sql} {order}", params).fetchall()
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_document(doc_id: str) -> dict[str, Any] | None:
    conn = db()
    cols = DOC_COLUMNS
    row = conn.execute(
        f"SELECT {','.join(cols)} FROM documents WHERE id=?", (doc_id,)
    ).fetchone()
    conn.close()
    return dict(zip(cols, row)) if row else None


def list_chunks(doc_id: str, sort: str = "") -> list[dict[str, Any]]:
    conn = db()
    cols = CHUNK_COLUMNS
    if sort == "authority":
        # Chunks inherit their document's authority; join to sort by it.
        rows = conn.execute(
            f"SELECT {','.join('c.'+c for c in cols)} FROM evidence_chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE c.document_id=? "
            "ORDER BY d.authority_level DESC, c.chunk_index",
            (doc_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {','.join(cols)} FROM evidence_chunks WHERE document_id=? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def list_jobs(status: str = "") -> list[dict[str, Any]]:
    conn = db()
    cols = JOB_COLUMNS
    if status:
        rows = conn.execute(
            f"SELECT {','.join(cols)} FROM import_jobs WHERE status=? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {','.join(cols)} FROM import_jobs ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(zip(cols, row))
        item["quarantined"] = bool(item["quarantined"])
        out.append(item)
    return out


# --- Phase 2A: deterministic retrieval and conservative citation checks -------

RRF_K = 60


def _cjk_chars(text: str) -> list[str]:
    return [ch for ch in text if '\u4e00' <= ch <= '\u9fff']


def tokenize_query(text: str) -> list[str]:
    """No-dependency mixed tokenizer for Chinese and ASCII policy text."""
    text = (text or "").lower()
    ascii_tokens = re.findall(r"[a-z0-9_]+", text)
    cjk = _cjk_chars(text)
    cjk_tokens = cjk + ["".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1))]
    return [t for t in ascii_tokens + cjk_tokens if t.strip()]


# BM25/FTS v1 term space: richer than tokenize_query's uni/bi-gram set. For CJK we
# emit contiguous-run ngrams of length 1..4 so multi-character policy terms (e.g.
# "现场核查", "专项资金") become weightable units with their own document
# frequency; ASCII/number tokens are kept whole. Stdlib only, fully deterministic.
BM25_CJK_NGRAM_MAX = 4


def _bm25_terms(text: str) -> list[str]:
    """Expand text into the BM25 term space (with repetition, for term frequency)."""
    text = (text or "").lower()
    terms: list[str] = re.findall(r"[a-z0-9_]+", text)
    # CJK ngrams within each contiguous run of Han characters.
    for run in re.findall(r"[一-鿿]+", text):
        n = len(run)
        for size in range(1, BM25_CJK_NGRAM_MAX + 1):
            if size > n:
                break
            for i in range(n - size + 1):
                terms.append(run[i:i + size])
    return [t for t in terms if t.strip()]


def _required_claim_markers(text: str) -> list[str]:
    """Markers that must be present in evidence before a claim can be supported.

    Keep the extractor encoding-robust: numeric facts are mandatory markers;
    policy-title brackets are handled by Unicode code points instead of literal
    non-ASCII regex text.
    """
    text = text or ""
    markers = re.findall(r"\d+(?:\.\d+)?(?:%|[A-Za-z]+)?", text)
    markers += re.findall("\u300a[^\u300b]{2,80}\u300b", text)
    markers += re.findall(r"[\u4e00-\u9fa5]{1,6}\u3014\d{4}\u3015\d+\u53f7", text)
    out = []
    for m in markers:
        if m and m not in out:
            out.append(m)
    return out


def _chunk_search_rows(conn: sqlite3.Connection, filters: dict[str, str]) -> list[dict[str, Any]]:
    cols = [
        "c.id", "c.document_id", "c.chunk_index", "c.char_start", "c.char_end", "c.status",
        "c.content", "c.location_kind", "c.location_value", "d.title", "d.source_url",
        "d.organization", "d.document_number", "d.publish_date", "d.status", "d.source_type",
        "d.authority_level", "d.region", "d.version", "d.format",
    ]
    names = [
        "chunk_id", "document_id", "chunk_index", "char_start", "char_end", "chunk_status",
        "content", "location_kind", "location_value", "document_title", "source_url",
        "organization", "document_number", "publish_date", "document_status", "source_type",
        "authority_level", "region", "version", "format",
    ]
    where = []
    params: list[Any] = []
    if filters.get("source_type"):
        where.append("d.source_type=?")
        params.append(normalize_source_type(filters["source_type"]))
    if filters.get("region"):
        where.append("d.region=?")
        params.append(filters["region"])
    if str(filters.get("organization", "")).strip():
        where.append("d.organization=?")
        params.append(filters["organization"].strip())
    if str(filters.get("format", "")).strip():
        # Normalize to lowercase without a leading dot, matching import storage.
        where.append("d.format=?")
        params.append(filters["format"].strip().lower().lstrip("."))
    if filters.get("status"):
        where.append("c.status=?")
        params.append(filters["status"])
    if filters.get("document_status"):
        where.append("d.status=?")
        params.append(normalize_status(filters["document_status"]))
    if filters.get("effective_only") in ("1", "true", "yes", "on", True):
        where.append("d.status='effective'")
        where.append("c.status='citable'")
    if str(filters.get("min_authority", "")).strip():
        try:
            where.append("d.authority_level>=?")
            params.append(int(filters["min_authority"]))
        except (TypeError, ValueError):
            pass
    if filters.get("date_from"):
        where.append("d.publish_date>=?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        where.append("d.publish_date<=?")
        params.append(filters["date_to"])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"SELECT {','.join(cols)} FROM evidence_chunks c JOIN documents d ON d.id=c.document_id{where_sql}",
        params,
    ).fetchall()
    return [dict(zip(names, row)) for row in rows]


def _searchable_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "") for k in (
        "content", "document_title", "organization", "document_number", "source_url", "region", "source_type"
    ))


def _rank_channel(rows: list[dict[str, Any]], score_fn) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        score, reason = score_fn(row)
        if score > 0:
            scored.append((score, row["chunk_id"], reason, row))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for idx, (score, _cid, reason, row) in enumerate(scored, start=1):
        out.append({"rank": idx, "score": score, "reason": reason, "row": row})
    return out


# BM25 tuning knobs (Okapi BM25). k1 controls term-frequency saturation; b
# controls document-length normalization. Kept as module constants so a later
# tuning round or evaluation harness can sweep them without touching call sites.
BM25_K1 = 1.5
BM25_B = 0.75
# Authority contributes only a minuscule tie-breaker so it can order otherwise
# equal textual matches without ever outranking real lexical support.
AUTHORITY_TIEBREAK = 0.001


def _resolve_bm25_params(bm25_params: dict[str, Any] | None) -> tuple[float, float]:
    """Return (k1, b) from an optional override dict, falling back to module defaults."""
    params = bm25_params or {}
    try:
        k1 = float(params.get("k1", BM25_K1))
    except (TypeError, ValueError):
        k1 = BM25_K1
    try:
        b = float(params.get("b", BM25_B))
    except (TypeError, ValueError):
        b = BM25_B
    # Guard against nonsensical values that would break the BM25 denominator.
    if k1 < 0:
        k1 = BM25_K1
    if not 0.0 <= b <= 1.0:
        b = BM25_B
    return k1, b


# --- Phase 2B: replaceable vector retrieval pipeline skeleton (v1) -----------
#
# This is a SKELETON, not a real semantic search stack. It is disabled by
# default and, when explicitly enabled in tests, uses only a deterministic,
# in-process, stdlib-only "embedder" (signed feature hashing over the same
# BM25 term space). It never calls an external API, never reads credentials,
# and never touches the network. Its purpose is to fix the extension seams so a
# future real embedding provider + vector index can drop in without reshaping
# `search_library`'s callers, fusion, or payload:
#
#   * VectorEmbedder            -- interface a real provider would implement.
#   * DeterministicHashEmbedder -- offline, reproducible embedder for tests only.
#   * InProcessVectorIndex      -- brute-force cosine index; swap for FAISS/pgvector later.
#   * VectorPipeline            -- ties embedder + index behind an `enabled` flag.
#   * resolve_vector_pipeline() -- turns an opt-in config into a pipeline (default: OFF).
#
# What is intentionally NOT here: any real embedding model, credential/.env
# handling, a persistent vector database, a real reranking model, or semantic/NLI
# reasoning. (A separate disabled-by-default reranker SKELETON follows below.)

VECTOR_DIM_DEFAULT = 256
VECTOR_MIN_SCORE_DEFAULT = 1e-9
# The only embedder mode this skeleton actually implements. A real provider mode
# (e.g. "provider_api") is deliberately absent so nothing can silently attempt a
# network/credentialed call; resolve_vector_pipeline() rejects unknown modes.
VECTOR_TEST_MODE = "deterministic_local_test"


class VectorEmbedder:
    """Extension point for a future real embedding provider.

    A real implementation (a hosted embedding API, a local model, etc.) would
    subclass this and implement ``embed``. This skeleton ships only the
    deterministic offline embedder below; no subclass here performs I/O.
    """

    mode = "abstract"

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class DeterministicHashEmbedder(VectorEmbedder):
    """Offline, reproducible embedder for tests only (NOT a semantic model).

    Uses signed feature hashing over the existing ``_bm25_terms`` term space:
    each term is hashed (SHA-256) to a bucket index and a sign, and its term
    frequency is accumulated into that bucket; the vector is then L2-normalized.
    Shared terms between a query and a document land in the same signed bucket,
    so cosine similarity rewards literal term overlap. This is deterministic and
    dependency-free, but it captures lexical co-occurrence only -- it does NOT
    model meaning, synonymy, or entailment. It exists purely to exercise the
    vector channel plumbing without any model, service, or key.
    """

    mode = VECTOR_TEST_MODE

    def __init__(self, dim: int = VECTOR_DIM_DEFAULT) -> None:
        self.dim = max(8, int(dim))

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for term, tf in Counter(_bm25_terms(text or "")).items():
            digest = hashlib.sha256(term.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if (digest[4] & 1) else -1.0
            vec[idx] += sign * float(tf)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        # Both vectors are unit-normalized on creation, so the dot product is the
        # cosine similarity. Guard length just in case of a dimension mismatch.
        n = min(len(a), len(b))
        return sum(a[i] * b[i] for i in range(n))


class InProcessVectorIndex:
    """Brute-force, in-memory cosine index (extension point for a real index).

    Holds ``(id, vector)`` pairs and does a linear cosine scan on query. This is
    fine for the small deterministic test corpora used here; a production build
    would replace this class with an ANN index or an external vector DB behind
    the same ``add``/``search`` surface. Nothing here persists to disk.
    """

    def __init__(self, embedder: VectorEmbedder) -> None:
        self.embedder = embedder
        self._items: list[tuple[str, list[float]]] = []

    def add(self, item_id: str, vector: list[float]) -> None:
        self._items.append((str(item_id), vector))

    def build(self, docs: list[tuple[str, str]]) -> "InProcessVectorIndex":
        """Embed and index ``(id, text)`` pairs. Deterministic, offline."""
        for item_id, text in docs:
            self.add(item_id, self.embedder.embed(text))
        return self

    def search(self, query: str, min_score: float = VECTOR_MIN_SCORE_DEFAULT) -> list[tuple[str, float]]:
        qvec = self.embedder.embed(query or "")
        hits = []
        for item_id, vec in self._items:
            score = DeterministicHashEmbedder.cosine(qvec, vec)
            if score > min_score:
                hits.append((item_id, score))
        hits.sort(key=lambda x: (-x[1], x[0]))
        return hits

    def __len__(self) -> int:
        return len(self._items)


class VectorPipeline:
    """Ties an embedder + index behind an explicit ``enabled`` flag.

    When disabled (the default everywhere), it contributes no channel and simply
    reports honest metadata. When enabled, it builds an in-process index over the
    candidate rows and exposes a ``rank`` list shaped exactly like the lexical
    channels so the existing RRF fusion consumes it with no special-casing.
    """

    def __init__(self, enabled: bool, mode: str, embedder: VectorEmbedder | None = None,
                 min_score: float = VECTOR_MIN_SCORE_DEFAULT, reason: str = "") -> None:
        self.enabled = bool(enabled)
        self.mode = mode
        self.embedder = embedder
        self.min_score = min_score
        self.reason = reason

    def metadata(self) -> dict[str, Any]:
        """JSON-serializable, honest description of the vector channel state."""
        meta: dict[str, Any] = {"enabled": self.enabled, "mode": self.mode, "reason": self.reason}
        if self.enabled and self.embedder is not None:
            meta["dim"] = getattr(self.embedder, "dim", None)
            # Be explicit that this is not a real semantic model.
            meta["is_real_embedding_model"] = False
            meta["min_score"] = self.min_score
        return meta

    def rank_rows(self, query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return ranked vector hits shaped like ``_rank_channel`` output."""
        if not self.enabled or self.embedder is None:
            return []
        index = InProcessVectorIndex(self.embedder)
        index.build([(row["chunk_id"], _searchable_text(row)) for row in rows])
        by_id = {row["chunk_id"]: row for row in rows}
        out = []
        for rank, (cid, score) in enumerate(index.search(query, self.min_score), start=1):
            row = by_id.get(cid)
            if row is None:
                continue
            reason = [f"vector_sim:{score:.4f}", f"vector_mode:{self.mode}"]
            out.append({"rank": rank, "score": score, "reason": reason, "row": row})
        return out


# Factory registry of AVAILABLE embedder modes. Only the deterministic offline
# test embedder is implemented; a real provider would register here (and only
# then read config/credentials). Absence keeps the skeleton network-free.
VECTOR_EMBEDDER_FACTORIES = {
    VECTOR_TEST_MODE: lambda dim: DeterministicHashEmbedder(dim=dim),
}


def _disabled_pipeline(reason: str) -> VectorPipeline:
    return VectorPipeline(enabled=False, mode="none", reason=reason)


def resolve_vector_pipeline(vector_config: Any) -> VectorPipeline:
    """Turn an opt-in config into a VectorPipeline. Default is DISABLED.

    Accepts:
      * ``None`` / falsy               -> disabled (the default, lexical/BM25 only);
      * ``True``                       -> enabled in deterministic local test mode;
      * a dict ``{"enabled": bool, "mode": str, "dim": int, "min_score": float}``.

    Only ``mode == "deterministic_local_test"`` is available. Any other mode
    (including a hypothetical real provider) resolves to DISABLED with an honest
    reason, so this code path can never attempt a network or credentialed call.
    """
    if not vector_config:
        return _disabled_pipeline(
            "vector retrieval disabled by default: deterministic lexical/BM25 only; no embeddings")
    if vector_config is True:
        vector_config = {"enabled": True, "mode": VECTOR_TEST_MODE}
    if not isinstance(vector_config, dict):
        return _disabled_pipeline(f"vector config ignored: unsupported type {type(vector_config).__name__}")
    if not vector_config.get("enabled", False):
        return _disabled_pipeline("vector retrieval explicitly disabled by config")

    mode = str(vector_config.get("mode") or VECTOR_TEST_MODE)
    factory = VECTOR_EMBEDDER_FACTORIES.get(mode)
    if factory is None:
        return _disabled_pipeline(
            f"vector mode {mode!r} is not available in this offline skeleton "
            f"(only {sorted(VECTOR_EMBEDDER_FACTORIES)} implemented; no external providers)")
    try:
        dim = int(vector_config.get("dim", VECTOR_DIM_DEFAULT))
    except (TypeError, ValueError):
        dim = VECTOR_DIM_DEFAULT
    try:
        min_score = float(vector_config.get("min_score", VECTOR_MIN_SCORE_DEFAULT))
    except (TypeError, ValueError):
        min_score = VECTOR_MIN_SCORE_DEFAULT
    embedder = factory(dim)
    return VectorPipeline(
        enabled=True, mode=mode, embedder=embedder, min_score=min_score,
        reason=("deterministic local test embedder (signed feature hashing over BM25 terms); "
                "offline, reproducible, NOT a real semantic embedding model"))


# Truthy tokens that opt an HTTP caller into the deterministic local test channel.
# Anything else (including empty/absent) keeps vector retrieval disabled.
VECTOR_ENABLE_TOKENS = {"1", "true", "yes", "on", "test", "deterministic", VECTOR_TEST_MODE}


def _vector_config_from_param(raw: str) -> Any:
    """Map the optional ``?vector=`` query param to a vector_config (default OFF).

    Only enables the offline deterministic test channel; it can never select a
    real provider (none exist in this skeleton), so no network/credential path
    is reachable from an HTTP request.
    """
    token = (raw or "").strip().lower()
    if token in VECTOR_ENABLE_TOKENS:
        return {"enabled": True, "mode": VECTOR_TEST_MODE}
    return None


# --- Phase 2B: pluggable reranker skeleton (v1) ------------------------------
#
# This is a SKELETON, not a real reranking model. It is disabled by default and,
# when explicitly enabled, uses only a deterministic, in-process, stdlib-only
# reranker that REORDERS the already-fused Top K candidates. It never retrieves
# new documents/chunks, never calls an external API, never reads credentials,
# and never touches the network. Its purpose is to fix the extension seams so a
# future real cross-encoder / reranking provider can drop in without reshaping
# `search_library`'s callers or payload:
#
#   * Reranker                 -- interface a real reranking provider would implement.
#   * DeterministicLocalReranker -- offline, reproducible reranker for tests only.
#   * RerankPipeline           -- ties a reranker behind an `enabled` flag.
#   * resolve_rerank_pipeline() -- turns an opt-in config into a pipeline (default: OFF).
#
# What is intentionally NOT here: any real reranking model, credential/.env
# handling, network access, a persistent index, or semantic/NLI reasoning.

RERANK_TEST_MODE = "deterministic_local_test"
RERANK_TOP_K_DEFAULT = 10


class Reranker:
    """Extension point for a future real reranking provider.

    A real implementation (a hosted cross-encoder, a local rerank model, etc.)
    would subclass this and implement ``score``. This skeleton ships only the
    deterministic offline reranker below; no subclass here performs I/O.
    """

    mode = "abstract"

    def score(self, query: str, item: dict[str, Any]) -> float:
        raise NotImplementedError


class DeterministicLocalReranker(Reranker):
    """Offline, reproducible reranker for tests only (NOT a rerank model).

    Assigns each candidate a deterministic relevance score from signals already
    present on the fused item -- query-term coverage over the candidate's
    searchable text, plus a tiny BM25-channel nudge -- so the ordering is stable
    and dependency-free. It reranks by re-scoring; it captures lexical overlap
    only and does NOT model relevance, semantics, or entailment. It exists purely
    to exercise the rerank plumbing without any model, service, or key.
    """

    mode = RERANK_TEST_MODE

    def score(self, query: str, item: dict[str, Any]) -> float:
        query_terms = set(_bm25_terms(query))
        if not query_terms:
            return 0.0
        text_terms = set(_bm25_terms(_searchable_text(item)))
        covered = query_terms & text_terms
        coverage = len(covered) / len(query_terms)
        # Small nudge from the existing bm25 channel score (if any) to break ties
        # deterministically without overriding coverage.
        bm25_score = 0.0
        channels = item.get("channels") or {}
        if isinstance(channels, dict) and isinstance(channels.get("bm25_like"), dict):
            bm25_score = float(channels["bm25_like"].get("score") or 0.0)
        return coverage + min(bm25_score, 10.0) * 1e-4


class RerankPipeline:
    """Ties a reranker behind an explicit ``enabled`` flag.

    When disabled (the default everywhere), ``apply`` returns the fused items
    unchanged and adds no per-item rerank details. When enabled, it re-scores and
    reorders only the candidates it is given (the already-fused, already-limited
    Top K) -- it never adds or fetches new items.
    """

    def __init__(self, enabled: bool, mode: str, reranker: Reranker | None = None,
                 top_k: int = RERANK_TOP_K_DEFAULT, reason: str = "") -> None:
        self.enabled = bool(enabled)
        self.mode = mode
        self.reranker = reranker
        self.top_k = max(1, int(top_k))
        self.reason = reason

    def metadata(self) -> dict[str, Any]:
        """JSON-serializable, honest description of the rerank state."""
        meta: dict[str, Any] = {"enabled": self.enabled, "mode": self.mode, "reason": self.reason}
        if self.enabled and self.reranker is not None:
            meta["top_k"] = self.top_k
            # Be explicit that this is not a real reranking model.
            meta["is_real_rerank_model"] = False
        return meta

    def apply(self, query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reorder (only) the given candidates. Never adds/fetches new items."""
        if not self.enabled or self.reranker is None or not items:
            return items
        window = min(self.top_k, len(items))
        head = items[:window]
        tail = items[window:]
        scored = []
        for original_rank, item in enumerate(head, start=1):
            score = self.reranker.score(query, item)
            # Attach inert-when-disabled, present-when-enabled rerank details.
            item["rerank"] = {"score": score, "original_rank": original_rank, "mode": self.mode}
            item["hit_reasons"] = sorted(set(item.get("hit_reasons", []) +
                                             [f"rerank_score:{score:.4f}", f"rerank_mode:{self.mode}"]))
            scored.append(item)
        # Stable deterministic reorder: higher rerank score first, original order
        # (then chunk_id) breaks ties so results are reproducible.
        scored.sort(key=lambda it: (-it["rerank"]["score"], it["rerank"]["original_rank"],
                                    str(it.get("chunk_id", ""))))
        return scored + tail


# Factory registry of AVAILABLE reranker modes. Only the deterministic offline
# reranker is implemented; a real provider would register here (and only then
# read config/credentials). Absence keeps the skeleton network-free.
RERANK_FACTORIES = {
    RERANK_TEST_MODE: lambda: DeterministicLocalReranker(),
}


def _disabled_rerank(reason: str) -> RerankPipeline:
    return RerankPipeline(enabled=False, mode="none", reason=reason)


def resolve_rerank_pipeline(rerank_config: Any) -> RerankPipeline:
    """Turn an opt-in config into a RerankPipeline. Default is DISABLED.

    Accepts:
      * ``None`` / falsy               -> disabled (default; fused order unchanged);
      * ``True``                       -> enabled in deterministic local test mode;
      * a dict ``{"enabled": bool, "mode": str, "top_k": int}``.

    Only ``mode == "deterministic_local_test"`` is available. Any other mode
    (including a hypothetical real provider) resolves to DISABLED with an honest
    reason, so this code path can never attempt a network or credentialed call.
    """
    if not rerank_config:
        return _disabled_rerank(
            "reranking disabled by default: fused RRF order preserved; no rerank model")
    if rerank_config is True:
        rerank_config = {"enabled": True, "mode": RERANK_TEST_MODE}
    if not isinstance(rerank_config, dict):
        return _disabled_rerank(f"rerank config ignored: unsupported type {type(rerank_config).__name__}")
    if not rerank_config.get("enabled", False):
        return _disabled_rerank("reranking explicitly disabled by config")

    mode = str(rerank_config.get("mode") or RERANK_TEST_MODE)
    factory = RERANK_FACTORIES.get(mode)
    if factory is None:
        return _disabled_rerank(
            f"rerank mode {mode!r} is not available in this offline skeleton "
            f"(only {sorted(RERANK_FACTORIES)} implemented; no external providers)")
    try:
        top_k = int(rerank_config.get("top_k", RERANK_TOP_K_DEFAULT))
    except (TypeError, ValueError):
        top_k = RERANK_TOP_K_DEFAULT
    reranker = factory()
    return RerankPipeline(
        enabled=True, mode=mode, reranker=reranker, top_k=top_k,
        reason=("deterministic local test reranker (query-term coverage over fused Top K); "
                "reorders current candidates only, NOT a real reranking model"))


# Truthy tokens that opt an HTTP caller into the deterministic local rerank mode.
# Anything else (including empty/absent) keeps reranking disabled.
RERANK_ENABLE_TOKENS = {"1", "true", "yes", "on", "test", "deterministic", RERANK_TEST_MODE}


def _rerank_config_from_param(raw: str) -> Any:
    """Map the optional ``?rerank=`` query param to a rerank_config (default OFF).

    Only enables the offline deterministic reranker; it can never select a real
    provider (none exist in this skeleton), so no network/credential path is
    reachable from an HTTP request.
    """
    token = (raw or "").strip().lower()
    if token in RERANK_ENABLE_TOKENS:
        return {"enabled": True, "mode": RERANK_TEST_MODE}
    return None


def search_library(query: str, filters: dict[str, str] | None = None, limit: int = 10,
                   bm25_params: dict[str, Any] | None = None,
                   vector_config: Any = None,
                   rerank_config: Any = None) -> dict[str, Any]:
    """Deterministic Phase 2B retrieval (BM25/FTS v1). No vector model is used.

    Three deterministic channels are fused with RRF:
    - ``lexical_exact``: exact query / required-marker substring hits;
    - ``fts_or_ngram``: token-overlap recall over the mixed CJK/ASCII tokenizer;
    - ``bm25_like``: Okapi BM25 over the richer CJK-ngram/ASCII term space, with
      IDF, term-frequency saturation (k1) and document-length normalization (b).

    ``bm25_params`` optionally overrides {"k1", "b"} for a tuning sweep; when omitted
    the module defaults (BM25_K1/BM25_B) are used and behavior is unchanged.

    ``vector_config`` is an OPT-IN, disabled-by-default hook for the Phase 2B vector
    retrieval skeleton (see resolve_vector_pipeline). When omitted/falsy the result is
    exactly lexical/BM25-only and ``result["vector"]["enabled"]`` is ``False`` (backward
    compatible). When enabled in deterministic local test mode it contributes a fourth
    RRF channel named ``vector`` with per-channel rank/score and ``vector_sim:*`` hit
    reasons. No external API, credential, or network access is ever used.

    ``rerank_config`` is an OPT-IN, disabled-by-default hook for the Phase 2B reranker
    skeleton (see resolve_rerank_pipeline). When omitted/falsy the fused RRF order is
    returned unchanged and no per-item ``rerank`` details are attached. When enabled in
    deterministic local test mode it REORDERS the already-fused, already-limited Top K
    candidates only (it never retrieves new chunks) and attaches per-item ``rerank``
    details. No external API, credential, or network access is ever used.

    Authority level only breaks ties; it never substitutes for textual support.
    """
    filters = filters or {}
    query = (query or "").strip()
    limit = max(1, min(int(limit or 10), 50))
    k1, b = _resolve_bm25_params(bm25_params)
    vector_pipeline = resolve_vector_pipeline(vector_config)
    rerank_pipeline = resolve_rerank_pipeline(rerank_config)
    tokens = tokenize_query(query)
    conn = db()
    try:
        rows = _chunk_search_rows(conn, filters)
    finally:
        conn.close()

    lowered_query = query.lower()

    def lexical(row):
        text = _searchable_text(row).lower()
        score = 0.0
        reasons = []
        if lowered_query and lowered_query in text:
            score += 10.0
            reasons.append("exact_query")
        for marker in _required_claim_markers(query):
            if marker.lower() in text:
                score += 3.0
                reasons.append(f"marker:{marker}")
        return score, reasons or ["no_exact_hit"]

    def ngram(row):
        text = _searchable_text(row).lower()
        matched = [t for t in tokens if t and t in text]
        if not matched:
            return 0.0, ["no_token_hit"]
        score = len(set(matched)) / max(1, len(set(tokens)))
        # Authority is a tie-breaker, not a substitute for textual support.
        score += min(float(row.get("authority_level") or 0), 6.0) / 100.0
        return score, ["token_overlap:" + ",".join(sorted(set(matched))[:8])]

    # --- BM25-like channel: precompute corpus statistics over the filtered rows.
    query_terms = set(_bm25_terms(query))
    doc_terms: dict[str, "Counter[str]"] = {}
    doc_len: dict[str, int] = {}
    doc_freq: dict[str, int] = {}
    for row in rows:
        terms = _bm25_terms(_searchable_text(row))
        counts = Counter(terms)
        cid = row["chunk_id"]
        doc_terms[cid] = counts
        doc_len[cid] = sum(counts.values())
        for term in counts:
            if term in query_terms:
                doc_freq[term] = doc_freq.get(term, 0) + 1
    n_docs = len(rows)
    avg_len = (sum(doc_len.values()) / n_docs) if n_docs else 0.0
    # Robust IDF (BM25): log(1 + (N - df + 0.5)/(df + 0.5)) is always positive.
    idf = {
        term: math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
        for term, df in doc_freq.items()
    }

    def bm25_like(row):
        if not query_terms or n_docs == 0:
            return 0.0, ["no_bm25_terms"]
        cid = row["chunk_id"]
        counts = doc_terms.get(cid, Counter())
        dl = doc_len.get(cid, 0)
        denom_norm = k1 * (1 - b + b * (dl / avg_len if avg_len else 0.0))
        score = 0.0
        matched: list[str] = []
        for term in query_terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            term_score = idf.get(term, 0.0) * (tf * (k1 + 1)) / (tf + denom_norm)
            if term_score > 0:
                score += term_score
                matched.append(term)
        if score <= 0:
            return 0.0, ["no_bm25_match"]
        score += min(float(row.get("authority_level") or 0), 6.0) * AUTHORITY_TIEBREAK
        top = sorted(matched, key=lambda t: (-idf.get(t, 0.0), t))[:8]
        return score, ["bm25:" + ",".join(top)]

    channels = {
        "lexical_exact": _rank_channel(rows, lexical),
        "fts_or_ngram": _rank_channel(rows, ngram),
        "bm25_like": _rank_channel(rows, bm25_like),
    }
    # Opt-in vector channel (disabled by default). When enabled it is just another
    # ranked list fused via the same RRF loop below -- no special-casing.
    if vector_pipeline.enabled:
        channels["vector"] = vector_pipeline.rank_rows(query, rows)
    fused: dict[str, dict[str, Any]] = {}
    for channel_name, ranked in channels.items():
        for item in ranked:
            cid = item["row"]["chunk_id"]
            entry = fused.setdefault(cid, {
                **item["row"], "fused_score": 0.0, "channels": {}, "hit_reasons": [],
            })
            entry["fused_score"] += 1.0 / (RRF_K + item["rank"])
            entry["channels"][channel_name] = {"rank": item["rank"], "score": item["score"]}
            entry["hit_reasons"].extend(item["reason"])
    results = list(fused.values())
    results.sort(key=lambda r: (-r["fused_score"], -(r.get("authority_level") or 0), r["chunk_id"]))
    for r in results:
        r["hit_reasons"] = sorted(set(r["hit_reasons"]))
    # Opt-in reranking (disabled by default): reorders only the already-fused,
    # already-limited Top K candidates -- it never retrieves new chunks. When
    # disabled, items is the fused order and carries no per-item `rerank` details.
    items = rerank_pipeline.apply(query, results[:limit])
    return {
        "query": query,
        "items": items,
        "channels": {name: len(vals) for name, vals in channels.items()},
        "bm25": {"k1": k1, "b": b, "cjk_ngram_max": BM25_CJK_NGRAM_MAX,
                 "corpus_size": n_docs, "avg_doc_len": avg_len},
        "vector": vector_pipeline.metadata(),
        "rerank": rerank_pipeline.metadata(),
    }


def map_claim_to_evidence(claim: str, evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministically map a claim's required markers and terms onto evidence chunks.

    Given retrieved ``evidence_items`` (the ``items`` from ``search_library``), report,
    per marker, which chunk covers it, which markers no chunk covers, and per chunk
    which markers/terms it matched. Stdlib only; this is lexical coverage, NOT a
    semantic entailment or conflict judgement (that stays out of scope in Phase 2B).

    Returns:
      - ``required_markers``: markers a claim must have supported (numbers, years,
        policy titles, document numbers) via ``_required_claim_markers``;
      - ``covered_markers``: {marker: [chunk_id, ...]} listing every chunk that
        contains the marker, in ranked order (a marker may be spread across
        several chunks, which matters for building a citation chain);
      - ``missing_markers``: markers no retrieved chunk contains;
      - ``supporting_items``: per-chunk detail for chunks matching >=1 marker or term;
      - ``coverage_ratio``: (markers covered by >=1 chunk)/required, or ``None``
        when the claim has no markers.
    """
    markers = _required_claim_markers(claim)
    claim_terms = set(tokenize_query(claim))

    covered_markers: dict[str, list[str]] = {}
    supporting_items: list[dict[str, Any]] = []
    for item in evidence_items or []:
        content = item.get("content") or ""
        chunk_id = item.get("chunk_id")
        matched_markers = [m for m in markers if m and m in content]
        # Record every chunk that covers a marker, in ranked order (deduped).
        for m in matched_markers:
            bucket = covered_markers.setdefault(m, [])
            if chunk_id not in bucket:
                bucket.append(chunk_id)
        content_terms = set(tokenize_query(content))
        matched_terms = sorted(claim_terms & content_terms)
        if matched_markers or matched_terms:
            supporting_items.append({
                "chunk_id": chunk_id,
                "document_id": item.get("document_id"),
                "document_title": item.get("document_title"),
                "source_type": item.get("source_type"),
                "authority_level": item.get("authority_level"),
                "matched_markers": matched_markers,
                "matched_terms": matched_terms[:12],
                "hit_reasons": item.get("hit_reasons", []),
            })

    missing_markers = [m for m in markers if m not in covered_markers]
    # coverage_ratio counts markers covered by at least one chunk over required.
    coverage_ratio = (len(covered_markers) / len(markers)) if markers else None
    return {
        "required_markers": markers,
        "covered_markers": covered_markers,
        "missing_markers": missing_markers,
        "supporting_items": supporting_items,
        "coverage_ratio": coverage_ratio,
    }


NEGATION_TERMS = (
    "不得", "禁止", "取消", "停止", "暂停", "不再", "未", "无", "没有",
    "not", "no ", "never", "without", "shall not", "must not",
)


def _claim_marker_groups(text: str) -> dict[str, list[str]]:
    markers = _required_claim_markers(text)
    years: list[str] = []
    numbers: list[str] = []
    named: list[str] = []
    for marker in markers:
        if re.fullmatch(r"(?:19|20)\d{2}", marker):
            years.append(marker)
        elif re.fullmatch(r"\d+(?:\.\d+)?(?:%|[A-Za-z]+)?", marker):
            numbers.append(marker)
        else:
            named.append(marker)
    return {"years": years, "numbers": numbers, "named": named}


def _has_near_negation(text: str, marker: str, window: int = 16) -> bool:
    lowered = text.lower()
    marker_lower = marker.lower()
    pos = lowered.find(marker_lower)
    while pos >= 0:
        start = max(0, pos - window)
        end = min(len(lowered), pos + len(marker_lower) + window)
        around = lowered[start:end]
        if any(term in around for term in NEGATION_TERMS):
            return True
        pos = lowered.find(marker_lower, pos + len(marker_lower))
    return False


def detect_conflict_evidence(claim: str, evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Conservative deterministic conflict-evidence detector.

    This v1 does not prove semantic contradiction. It flags narrow, auditable
    lexical conflicts inside retrieved chunks:
    - same claim context/year, but a different numeric marker appears instead of
      the claim's numeric marker;
    - a required marker is present near explicit negation words.

    The output is advisory and should force human verification rather than an
    automatic unsupported verdict.
    """
    groups = _claim_marker_groups(claim)
    claim_terms = set(tokenize_query(claim))
    claim_numbers = set(groups["numbers"])
    claim_years = set(groups["years"])
    conflicts: list[dict[str, Any]] = []

    for item in evidence_items or []:
        content = item.get("content") or ""
        content_terms = set(tokenize_query(content))
        matched_terms = sorted(claim_terms & content_terms)
        if len(matched_terms) < 2:
            continue

        content_groups = _claim_marker_groups(content)
        content_numbers = set(content_groups["numbers"])
        content_years = set(content_groups["years"])
        shared_year = sorted(claim_years & content_years)
        shared_named = [m for m in groups["named"] if m in content]

        context_matches = bool(shared_year or shared_named or len(matched_terms) >= 3)
        if context_matches and claim_numbers:
            different_numbers = sorted(content_numbers - claim_numbers)
            missing_claim_numbers = sorted(claim_numbers - content_numbers)
            if different_numbers and missing_claim_numbers:
                conflicts.append({
                    "chunk_id": item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "document_title": item.get("document_title"),
                    "conflict_type": "different_numeric_marker",
                    "claim_markers": missing_claim_numbers,
                    "evidence_markers": different_numbers,
                    "shared_years": shared_year,
                    "matched_terms": matched_terms[:12],
                    "hit_reasons": item.get("hit_reasons", []),
                })

        negated = [m for m in groups["numbers"] + groups["years"] + groups["named"]
                   if m in content and _has_near_negation(content, m)]
        if negated:
            conflicts.append({
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "document_title": item.get("document_title"),
                "conflict_type": "negated_required_marker",
                "claim_markers": negated,
                "evidence_markers": negated,
                "shared_years": shared_year,
                "matched_terms": matched_terms[:12],
                "hit_reasons": item.get("hit_reasons", []),
            })

    # Stable de-duplication if both rules report the same chunk/type.
    deduped: list[dict[str, Any]] = []
    seen = set()
    for conflict in conflicts:
        key = (conflict.get("chunk_id"), conflict.get("conflict_type"),
               tuple(conflict.get("claim_markers") or ()),
               tuple(conflict.get("evidence_markers") or ()))
        if key not in seen:
            seen.add(key)
            deduped.append(conflict)

    return {
        "has_conflicts": bool(deduped),
        "items": deduped,
        "summary": "conflict_candidates_found" if deduped else "no_deterministic_conflict_found",
        "method": "deterministic_lexical_v1",
    }


def build_evidence_insufficiency(status: str, items: list[dict[str, Any]],
                                 evidence_map: dict[str, Any],
                                 conflict_evidence: dict[str, Any],
                                 claim_tokens: set[str], overlap: set[str],
                                 downgraded_by_conflict: bool = False) -> dict[str, Any]:
    """Deterministic, auditable evidence-insufficiency / refusal detail (v1).

    Explains -- in machine-readable, JSON-serializable form -- WHY a claim is not
    safely supported, from the same conservative lexical signals ``verify_claim``
    already computed. This is deterministic lexical audit metadata: it is NOT
    semantic entailment, NLI, a true-contradiction proof, or any model judgement.

    ``blocking`` is true whenever the result must not be treated as supported
    (i.e. any status other than ``supported``). ``has_insufficiency`` is true
    whenever there is something to explain (summary != ``none``). The ``details``
    list carries stable reason objects (each with a ``code``), not prose-only
    strings, so callers/UIs can branch on them.
    """
    missing_markers = list(evidence_map.get("missing_markers") or [])
    required_markers = list(evidence_map.get("required_markers") or [])
    conflict_items = list(conflict_evidence.get("items") or [])
    conflict_count = len(conflict_items)
    coverage_ratio = evidence_map.get("coverage_ratio")

    overlap_info = {
        "claim_token_count": len(claim_tokens),
        "overlap_token_count": len(overlap),
        "overlap_ratio": (len(overlap) / len(claim_tokens)) if claim_tokens else None,
        "coverage_ratio": coverage_ratio,
    }

    details: list[dict[str, Any]] = []
    if not items:
        details.append({"code": "no_retrieved_evidence",
                        "message": "no chunks were retrieved for this claim"})
    if missing_markers:
        details.append({"code": "required_markers_missing",
                        "markers": missing_markers,
                        "message": "required markers are not covered by any retrieved chunk"})
    if conflict_count:
        details.append({"code": "conflict_candidates_found",
                        "conflict_count": conflict_count,
                        "conflict_types": sorted({str(c.get("conflict_type")) for c in conflict_items}),
                        "downgraded_from_supported": bool(downgraded_by_conflict),
                        "message": "deterministic lexical conflict candidates were found (not a proven contradiction)"})
    # Weak lexical support: retrieved something, no missing markers, but either no
    # required markers at all or too little token overlap to be safely supported.
    if items and not missing_markers and status != "supported":
        details.append({"code": "weak_lexical_overlap",
                        "required_marker_count": len(required_markers),
                        "overlap_token_count": len(overlap),
                        "message": ("support is only lexical overlap, not semantic entailment; "
                                    "insufficient to treat as supported")})

    # Pick a single stable summary by priority (most fundamental gap first).
    if not items:
        summary = "no_retrieved_evidence"
    elif missing_markers:
        summary = "required_markers_missing"
    elif downgraded_by_conflict or (conflict_count and status != "supported"):
        summary = "conflict_candidates_found"
    elif items and status != "supported":
        summary = "weak_lexical_overlap"
    else:
        summary = "none"

    blocking = status != "supported"
    return {
        "has_insufficiency": summary != "none",
        "summary": summary,
        "blocking": blocking,
        "missing_markers": missing_markers,
        "conflict_count": conflict_count,
        "overlap": overlap_info,
        "details": details,
        "method": "deterministic_lexical_insufficiency_v1",
    }


def verify_claim(claim: str, filters: dict[str, str] | None = None, limit: int = 5) -> dict[str, Any]:
    """Conservative lexical claim support check over retrieved chunks.

    Uses ``map_claim_to_evidence`` to attribute each required marker to a covering
    chunk and to expose per-chunk supporting detail (``evidence_map``). The status
    stays conservative: any required marker (number/year/policy title/doc number)
    not covered by some retrieved chunk means the claim is never ``supported``.
    """
    result = search_library(claim, filters=filters, limit=limit)
    items = result["items"]
    evidence_map = map_claim_to_evidence(claim, items)
    conflict_evidence = detect_conflict_evidence(claim, items)
    markers = evidence_map["required_markers"]
    missing = evidence_map["missing_markers"]

    # Lexical token overlap between the claim and all retrieved evidence. Computed
    # once (deterministically) so both the status logic and the insufficiency audit
    # below use the same numbers. Safe when there are no items (empty overlap).
    combined = "\n".join(item.get("content") or "" for item in items)
    claim_tokens = set(tokenize_query(claim))
    evidence_tokens = set(tokenize_query(combined))
    overlap = claim_tokens & evidence_tokens

    if not items:
        status = "unsupported"
        reasons = ["no_retrieved_evidence"]
    elif missing:
        status = "needs_verification"
        reasons = ["required_markers_missing:" + ",".join(missing)]
    else:
        if markers and overlap:
            status = "supported"
            reasons = ["required_markers_present", "lexical_overlap"]
        elif len(overlap) >= max(2, min(5, len(claim_tokens))):
            status = "needs_verification"
            reasons = ["lexical_overlap_without_required_markers"]
        else:
            status = "unsupported"
            reasons = ["insufficient_lexical_overlap"]

    downgraded_by_conflict = False
    if conflict_evidence["has_conflicts"] and status == "supported":
        status = "needs_verification"
        reasons = ["conflict_evidence_detected"] + reasons
        downgraded_by_conflict = True

    # Cite chunks that actually matched a marker first (most defensible), then fall
    # back to the top retrieved items so a citation list is still returned.
    marker_chunk_ids = [it["chunk_id"] for it in evidence_map["supporting_items"]
                        if it["matched_markers"]]
    cited = list(dict.fromkeys(marker_chunk_ids))
    if not cited:
        cited = [item["chunk_id"] for item in items[:limit]]
    else:
        for item in items[:limit]:
            if item["chunk_id"] not in cited:
                cited.append(item["chunk_id"])

    insufficiency = build_evidence_insufficiency(
        status=status, items=items, evidence_map=evidence_map,
        conflict_evidence=conflict_evidence, claim_tokens=claim_tokens,
        overlap=overlap, downgraded_by_conflict=downgraded_by_conflict)

    return {
        "claim": claim,
        "status": status,
        "required_markers": markers,
        "missing_markers": missing,
        "cited_chunk_ids": cited[:limit],
        "evidence_map": evidence_map,
        "conflict_evidence": conflict_evidence,
        "insufficiency": insufficiency,
        "reasons": reasons,
        "search": result,
    }


def recall_at_k(results: list[str], relevant: set[str], k: int = 10) -> float:
    if not relevant:
        return 0.0
    return len(set(results[:k]) & set(relevant)) / len(set(relevant))


def mean_reciprocal_rank(ranked_lists: list[list[str]], relevant_sets: list[set[str]]) -> float:
    if not ranked_lists:
        return 0.0
    total = 0.0
    for ranked, relevant in zip(ranked_lists, relevant_sets):
        rr = 0.0
        for idx, item in enumerate(ranked, start=1):
            if item in relevant:
                rr = 1.0 / idx
                break
        total += rr
    return total / len(ranked_lists)


def evaluate_retrieval_cases(cases: list[dict[str, Any]], k: int = 10,
                             bm25_params: dict[str, Any] | None = None,
                             vector_config: Any = None,
                             rerank_config: Any = None) -> dict[str, Any]:
    """Run a deterministic retrieval benchmark over library search.

    Phase 2B needs a stable benchmark harness before BM25 tuning, embeddings, or
    reranking. Cases name relevant document titles or chunk ids; the evaluator
    reports both title-level and chunk-level metrics so early anonymous suites
    can start coarse and later become more precise.

    Each case additionally reports which labeled answers were missed within Top K
    (`missed_titles`, `missed_chunk_ids`) and the rank of the first relevant hit
    (`first_relevant_rank`), and the aggregate `misses` list carries the same
    diagnostics so a quality gate can point at concrete gaps, not just a number.

    For auditability each case also reports `top_reasons`: the Top K results with
    their fused score, per-channel ranks/scores and hit reasons, so a reviewer can
    see why a chunk ranked where it did (BM25 terms, token overlap, exact hits).
    """
    k = max(1, min(int(k or 10), 50))
    evaluated = []
    title_ranked_lists: list[list[str]] = []
    title_relevant_sets: list[set[str]] = []
    chunk_ranked_lists: list[list[str]] = []
    chunk_relevant_sets: list[set[str]] = []

    for idx, case in enumerate(cases or [], start=1):
        query = str(case.get("query", "")).strip()
        filters = case.get("filters", {}) or {}
        result = search_library(query, filters=filters, limit=k, bm25_params=bm25_params,
                                vector_config=vector_config, rerank_config=rerank_config)
        items = result.get("items", [])
        ranked_titles = [str(item.get("document_title", "")) for item in items]
        ranked_chunks = [str(item.get("chunk_id", "")) for item in items]
        relevant_titles = {str(v) for v in case.get("relevant_titles", []) if str(v).strip()}
        relevant_chunks = {str(v) for v in case.get("relevant_chunk_ids", []) if str(v).strip()}

        title_recall = recall_at_k(ranked_titles, relevant_titles, k) if relevant_titles else None
        chunk_recall = recall_at_k(ranked_chunks, relevant_chunks, k) if relevant_chunks else None
        if relevant_titles:
            title_ranked_lists.append(ranked_titles)
            title_relevant_sets.append(relevant_titles)
        if relevant_chunks:
            chunk_ranked_lists.append(ranked_chunks)
            chunk_relevant_sets.append(relevant_chunks)

        # Per-case diagnostics: which labeled answers were retrieved within Top K
        # and which were missed, so an anonymous suite can point at concrete gaps
        # instead of only reporting an aggregate number.
        top_title_set = set(ranked_titles[:k])
        top_chunk_set = set(ranked_chunks[:k])
        missed_titles = sorted(relevant_titles - top_title_set)
        missed_chunk_ids = sorted(relevant_chunks - top_chunk_set)
        first_relevant_rank = None
        for pos, (t, c) in enumerate(zip(ranked_titles, ranked_chunks), start=1):
            if (relevant_titles and t in relevant_titles) or (relevant_chunks and c in relevant_chunks):
                first_relevant_rank = pos
                break

        # Explainability: why each Top K chunk ranked where it did.
        top_reasons = [{
            "rank": pos,
            "chunk_id": str(item.get("chunk_id", "")),
            "document_title": str(item.get("document_title", "")),
            "fused_score": item.get("fused_score"),
            "channels": item.get("channels", {}),
            "hit_reasons": item.get("hit_reasons", []),
        } for pos, item in enumerate(items[:k], start=1)]

        evaluated.append({
            "id": case.get("id") or f"case-{idx}",
            "query": query,
            "top_titles": ranked_titles[:k],
            "top_chunk_ids": ranked_chunks[:k],
            "top_reasons": top_reasons,
            "relevant_titles": sorted(relevant_titles),
            "relevant_chunk_ids": sorted(relevant_chunks),
            "title_recall_at_k": title_recall,
            "chunk_recall_at_k": chunk_recall,
            "missed_titles": missed_titles,
            "missed_chunk_ids": missed_chunk_ids,
            "first_relevant_rank": first_relevant_rank,
            "hit": bool(
                (relevant_titles and top_title_set & relevant_titles) or
                (relevant_chunks and top_chunk_set & relevant_chunks)
            ),
        })

    title_recall_values = [c["title_recall_at_k"] for c in evaluated if c["title_recall_at_k"] is not None]
    chunk_recall_values = [c["chunk_recall_at_k"] for c in evaluated if c["chunk_recall_at_k"] is not None]
    misses = [c for c in evaluated if not c["hit"]]
    return {
        "case_count": len(evaluated),
        "k": k,
        "title_recall_at_k": (sum(title_recall_values) / len(title_recall_values)) if title_recall_values else 0.0,
        "title_mrr": mean_reciprocal_rank(title_ranked_lists, title_relevant_sets) if title_ranked_lists else 0.0,
        "chunk_recall_at_k": (sum(chunk_recall_values) / len(chunk_recall_values)) if chunk_recall_values else 0.0,
        "chunk_mrr": mean_reciprocal_rank(chunk_ranked_lists, chunk_relevant_sets) if chunk_ranked_lists else 0.0,
        "miss_count": len(misses),
        "misses": [{
            "id": c["id"],
            "query": c["query"],
            "missed_titles": c["missed_titles"],
            "missed_chunk_ids": c["missed_chunk_ids"],
        } for c in misses],
        "cases": evaluated,
        "bm25": {"k1": _resolve_bm25_params(bm25_params)[0],
                 "b": _resolve_bm25_params(bm25_params)[1],
                 "cjk_ngram_max": BM25_CJK_NGRAM_MAX},
        # Honest vector state: mirrors whatever search_library actually used. With no
        # vector_config (the eval-retrieval quality gate default) this stays disabled,
        # so the deterministic benchmark needs no embeddings.
        "vector": resolve_vector_pipeline(vector_config).metadata(),
        # Honest rerank state: disabled by default (the eval-retrieval quality gate
        # default), so the deterministic benchmark needs no reranking model.
        "rerank": resolve_rerank_pipeline(rerank_config).metadata(),
    }


# --- Phase 2B: reusable eval-suite loader/runner (shared by tests and CLI) ----

def load_retrieval_eval_suite(path: str | Path) -> dict[str, Any]:
    """Load a retrieval eval suite JSON (with `corpus` and `cases`) into a dict."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        suite = json.load(f)
    if not isinstance(suite, dict):
        raise ValueError(f"eval suite must be a JSON object, got {type(suite).__name__}")
    if not isinstance(suite.get("cases"), list):
        raise ValueError("eval suite is missing a 'cases' list")
    return suite


def _import_suite_corpus(suite: dict[str, Any]) -> None:
    """Import every corpus document of a suite into the current DB_PATH."""
    for doc in suite.get("corpus", []) or []:
        res = import_document({
            "title": doc.get("title", ""),
            "format": doc.get("format", "txt"),
            "text": doc.get("text", ""),
            "status": doc.get("status", "有效"),
            "source_type": doc.get("source_type", ""),
            "region": doc.get("region", ""),
            "document_number": doc.get("document_number", ""),
        })
        if res.get("status") not in ("succeeded", "new_version"):
            raise ValueError(f"corpus doc failed to import: {doc.get('title')!r} -> {res}")


def build_suite_cases(suite: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve a suite's `relevant_chunk_markers` into concrete `relevant_chunk_ids`.

    Assumes the suite corpus is already imported into the current DB_PATH. Raises
    ValueError if any declared marker does not resolve to a chunk (drift guard).
    """
    all_chunks: list[dict[str, Any]] = []
    for d in list_documents():
        all_chunks.extend(list_chunks(d["id"]))

    def resolve(marker: str, case_id: str) -> str:
        norm = _norm_line(marker)
        for c in all_chunks:
            if norm and norm in _norm_line(c["content"]):
                return c["id"]
        raise ValueError(f"marker did not resolve to any chunk: {marker!r} in case {case_id}")

    cases: list[dict[str, Any]] = []
    for idx, case in enumerate(suite.get("cases", []) or [], start=1):
        case_id = case.get("id") or f"case-{idx}"
        chunk_ids = [resolve(m, case_id) for m in case.get("relevant_chunk_markers", []) or []]
        # Explicit relevant_chunk_ids (already concrete) are kept as-is too.
        chunk_ids += [str(v) for v in case.get("relevant_chunk_ids", []) or [] if str(v).strip()]
        cases.append({
            "id": case_id,
            "query": case.get("query", ""),
            "filters": case.get("filters", {}) or {},
            "relevant_titles": case.get("relevant_titles", []) or [],
            "relevant_chunk_ids": chunk_ids,
        })
    return cases


# Filter keys a retrieval eval case may legitimately use (mirrors _chunk_search_rows).
SUPPORTED_FILTER_KEYS = {
    "effective_only", "source_type", "min_authority", "region", "organization",
    "format", "date_from", "date_to", "document_status", "status",
}


def validate_retrieval_suite(suite: Any) -> dict[str, Any]:
    """Validate a retrieval eval suite's shape without touching the DB or corpus.

    Checks suite- and case-level structure so a future human-provided real
    anonymized suite can be vetted before it becomes a quality gate. Stdlib only;
    reads no document content into the report. Returns:
      {passed, errors, warnings, case_count, filter_keys_used, relevance_target_counts}
    Errors fail validation; warnings do not (e.g. small/placeholder suites).
    """
    errors: list[str] = []
    warnings: list[str] = []
    filter_keys_used: set[str] = set()
    relevance_target_counts = {"relevant_titles": 0, "relevant_chunk_ids": 0, "relevant_chunk_markers": 0}

    if not isinstance(suite, dict):
        return {"passed": False, "errors": [f"suite must be a JSON object, got {type(suite).__name__}"],
                "warnings": [], "case_count": 0, "filter_keys_used": [],
                "relevance_target_counts": relevance_target_counts}

    for field in ("suite", "name"):
        if field in suite and not (isinstance(suite[field], str) and suite[field].strip()):
            errors.append(f"suite-level '{field}' must be a non-empty string when present")

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("suite must contain a non-empty 'cases' list")
        cases = []

    seen_ids: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        where = f"case #{idx}"
        if not isinstance(case, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = case.get("id")
        if not (isinstance(cid, str) and cid.strip()):
            errors.append(f"{where}: 'id' must be a non-empty string")
        else:
            where = f"case '{cid}'"
            if cid in seen_ids:
                errors.append(f"{where}: duplicate id")
            seen_ids.add(cid)

        query = case.get("query")
        if not (isinstance(query, str) and query.strip()):
            errors.append(f"{where}: 'query' must be a non-empty string")

        filters = case.get("filters", {})
        if filters in (None, {}):
            filters = {}
        elif not isinstance(filters, dict):
            errors.append(f"{where}: 'filters' must be an object")
            filters = {}
        for key in filters:
            filter_keys_used.add(key)
            if key not in SUPPORTED_FILTER_KEYS:
                errors.append(f"{where}: unsupported filter key '{key}'")
        if str(filters.get("min_authority", "")).strip():
            try:
                int(filters["min_authority"])
            except (TypeError, ValueError):
                errors.append(f"{where}: filter 'min_authority' must parse to an integer")
        if str(filters.get("format", "")).strip():
            fmt = str(filters["format"]).strip().lower().lstrip(".")
            if fmt not in SUPPORTED_FORMATS:
                warnings.append(f"{where}: filter format '{fmt}' is not a supported format {sorted(SUPPORTED_FORMATS)}")

        # Relevance targets: at least one, each a list of non-empty strings when present.
        has_target = False
        for tkey in ("relevant_titles", "relevant_chunk_ids", "relevant_chunk_markers"):
            if tkey not in case:
                continue
            val = case[tkey]
            if not isinstance(val, list):
                errors.append(f"{where}: '{tkey}' must be a list when present")
                continue
            if any(not (isinstance(v, str) and v.strip()) for v in val):
                errors.append(f"{where}: '{tkey}' must contain only non-empty strings")
            if val:
                has_target = True
                relevance_target_counts[tkey] += 1
        if not has_target:
            errors.append(f"{where}: must have at least one non-empty relevance target "
                          "(relevant_titles / relevant_chunk_ids / relevant_chunk_markers)")

    case_count = len(cases)
    # Warn (not fail) on small or explicitly-placeholder suites.
    meta_blob = " ".join(str(suite.get(k, "")) for k in ("suite", "name", "description")).lower()
    if any(word in meta_blob for word in ("placeholder", "synthetic", "占位", "合成")):
        warnings.append("suite metadata marks it as placeholder/synthetic; not a real anonymized set")
    if case_count and case_count < 50:
        warnings.append(f"case_count {case_count} < 50; a real anonymized suite should have 50-100 cases")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_count": case_count,
        "filter_keys_used": sorted(filter_keys_used),
        "relevance_target_counts": relevance_target_counts,
    }


# --- Stage 5: benchmark suite schema + deterministic scoring skeleton v1 ------

# The four scoring dimensions a benchmark case declares expected elements for.
BENCHMARK_DIMENSIONS = ("facts", "citations", "structure", "language")


def load_benchmark_suite(path: str | Path) -> dict[str, Any]:
    """Load a benchmark suite JSON (metadata + cases) into a dict.

    Stdlib only. Raises ValueError on a non-object payload or a missing cases
    list so callers can distinguish a load failure from a validation failure.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        suite = json.load(f)
    if not isinstance(suite, dict):
        raise ValueError(f"benchmark suite must be a JSON object, got {type(suite).__name__}")
    if not isinstance(suite.get("cases"), list):
        raise ValueError("benchmark suite is missing a 'cases' list")
    return suite


def validate_benchmark_suite(suite: Any) -> dict[str, Any]:
    """Validate a benchmark suite's shape without any model or network call.

    Schema (v1):
      metadata: {name, version, anonymized: true, ...}
      cases[]: {id, genre, prompt_fields{}, facts[], evidence[],
                expected_elements{facts[], citations[], structure[], language[]}}

    Deterministic and stdlib only. Reads no external data. Errors fail
    validation; warnings (small / non-anonymized-looking / placeholder) do not.
    Returns {passed, errors, warnings, case_count, genres, expected_element_counts}.
    """
    errors: list[str] = []
    warnings: list[str] = []
    genres: set[str] = set()
    expected_element_counts = {dim: 0 for dim in BENCHMARK_DIMENSIONS}

    if not isinstance(suite, dict):
        return {"passed": False, "errors": [f"suite must be a JSON object, got {type(suite).__name__}"],
                "warnings": [], "case_count": 0, "genres": [],
                "expected_element_counts": expected_element_counts}

    metadata = suite.get("metadata", {})
    if not isinstance(metadata, dict):
        errors.append("'metadata' must be an object")
        metadata = {}
    else:
        for field in ("name", "version"):
            if not (isinstance(metadata.get(field), str) and metadata[field].strip()):
                errors.append(f"metadata '{field}' must be a non-empty string")
        if metadata.get("anonymized") is not True:
            errors.append("metadata 'anonymized' must be true (only anonymized sets are accepted)")

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("suite must contain a non-empty 'cases' list")
        cases = []

    seen_ids: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        where = f"case #{idx}"
        if not isinstance(case, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = case.get("id")
        if not (isinstance(cid, str) and cid.strip()):
            errors.append(f"{where}: 'id' must be a non-empty string")
        else:
            where = f"case '{cid}'"
            if cid in seen_ids:
                errors.append(f"{where}: duplicate id")
            seen_ids.add(cid)

        if not (isinstance(case.get("genre"), str) and case["genre"].strip()):
            errors.append(f"{where}: 'genre' must be a non-empty string")
        else:
            genres.add(case["genre"].strip())

        for lkey in ("prompt_fields",):
            val = case.get(lkey, {})
            if val not in (None, {}) and not isinstance(val, dict):
                errors.append(f"{where}: '{lkey}' must be an object when present")
        for lkey in ("facts", "evidence"):
            val = case.get(lkey, [])
            if val not in (None, []) and not isinstance(val, list):
                errors.append(f"{where}: '{lkey}' must be a list when present")

        expected = case.get("expected_elements")
        if not isinstance(expected, dict):
            errors.append(f"{where}: 'expected_elements' must be an object")
            continue
        unknown = set(expected) - set(BENCHMARK_DIMENSIONS)
        if unknown:
            errors.append(f"{where}: unknown expected_elements dimension(s) {sorted(unknown)}")
        has_any = False
        for dim in BENCHMARK_DIMENSIONS:
            markers = expected.get(dim, [])
            if markers in (None, []):
                continue
            if not isinstance(markers, list):
                errors.append(f"{where}: expected_elements '{dim}' must be a list when present")
                continue
            if any(not (isinstance(m, str) and m.strip()) for m in markers):
                errors.append(f"{where}: expected_elements '{dim}' must contain only non-empty strings")
            if markers:
                has_any = True
                expected_element_counts[dim] += len(markers)
        if not has_any:
            errors.append(f"{where}: expected_elements must declare at least one non-empty dimension")

    case_count = len(cases)
    meta_blob = " ".join(str(metadata.get(k, "")) for k in ("name", "version", "description")).lower()
    if any(word in meta_blob for word in ("placeholder", "synthetic", "sample", "占位", "合成", "样例")):
        warnings.append("metadata marks the suite as placeholder/synthetic; not a real anonymized set")
    if case_count and case_count < 50:
        warnings.append(f"case_count {case_count} < 50; a real anonymized benchmark should have 50-100 cases")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_count": case_count,
        "genres": sorted(genres),
        "expected_element_counts": expected_element_counts,
    }


def _benchmark_marker_coverage(markers: list[str], candidate: str) -> dict[str, Any]:
    """Deterministic lexical coverage of expected markers within candidate text."""
    norm_candidate = _norm_line(candidate).lower()
    total = len(markers)
    matched: list[str] = []
    missing: list[str] = []
    for marker in markers:
        norm_marker = _norm_line(str(marker)).lower()
        if norm_marker and norm_marker in norm_candidate:
            matched.append(marker)
        else:
            missing.append(marker)
    score = (len(matched) / total) if total else 1.0
    return {
        "score": round(score, 4),
        "expected": total,
        "matched": len(matched),
        "missing": missing,
    }


def score_benchmark_suite(suite: dict[str, Any],
                          responses: dict[str, str] | None = None) -> dict[str, Any]:
    """Score candidate answers against a benchmark suite by lexical marker coverage.

    Deterministic scoring skeleton (v1): for each case, each dimension score is
    the fraction of that dimension's expected markers found (substring, case- and
    whitespace-normalized) in the candidate text. The candidate text is taken from
    ``responses[case_id]`` when provided, otherwise from the case's own
    ``reference_answer`` field (so the synthetic fixture is self-scoring). A case
    with no candidate text scores 0 on every declared dimension.

    No model call, no network, no persistence. Returns per-case dimension scores
    plus aggregate mean dimension scores and an overall mean.
    """
    responses = responses or {}
    per_case: list[dict[str, Any]] = []
    # Accumulate per-dimension sums over cases that declare that dimension.
    dim_sums = {dim: 0.0 for dim in BENCHMARK_DIMENSIONS}
    dim_counts = {dim: 0 for dim in BENCHMARK_DIMENSIONS}

    for idx, case in enumerate(suite.get("cases", []) or [], start=1):
        if not isinstance(case, dict):
            continue
        cid = case.get("id") or f"case-{idx}"
        candidate = responses.get(cid)
        if candidate is None:
            candidate = case.get("reference_answer", "")
        candidate = str(candidate or "")
        expected = case.get("expected_elements", {}) if isinstance(case.get("expected_elements"), dict) else {}

        dimensions: dict[str, Any] = {}
        declared_scores: list[float] = []
        for dim in BENCHMARK_DIMENSIONS:
            markers = expected.get(dim, [])
            if not isinstance(markers, list) or not markers:
                continue
            markers = [str(m) for m in markers if str(m).strip()]
            if not markers:
                continue
            coverage = _benchmark_marker_coverage(markers, candidate)
            dimensions[dim] = coverage
            dim_sums[dim] += coverage["score"]
            dim_counts[dim] += 1
            declared_scores.append(coverage["score"])

        overall = round(sum(declared_scores) / len(declared_scores), 4) if declared_scores else 0.0
        per_case.append({
            "id": cid,
            "genre": case.get("genre", ""),
            "has_candidate": bool(candidate.strip()),
            "dimensions": dimensions,
            "overall": overall,
        })

    aggregate_dimensions = {
        dim: round(dim_sums[dim] / dim_counts[dim], 4) if dim_counts[dim] else None
        for dim in BENCHMARK_DIMENSIONS
    }
    case_overalls = [c["overall"] for c in per_case]
    aggregate_overall = round(sum(case_overalls) / len(case_overalls), 4) if case_overalls else 0.0

    return {
        "method": "benchmark_lexical_scoring_v1",
        "boundary": (
            "deterministic lexical marker coverage only; not a semantic, factual, "
            "or human-judgment quality score, and no model is invoked"
        ),
        "case_count": len(per_case),
        "cases": per_case,
        "aggregate": {
            "dimensions": aggregate_dimensions,
            "overall": aggregate_overall,
            "scored_dimension_counts": dim_counts,
        },
    }


# --- Stage 5: blind evaluation packaging + reveal skeleton v1 -----------------

# Identity fields that must never appear on an evaluator-facing candidate item.
BLIND_IDENTITY_FIELDS = frozenset({
    "provider", "model", "version", "vendor", "engine", "system", "family", "name",
})


def _blind_label(index: int) -> str:
    """Deterministic stable blind label: candidate_a, candidate_b, ... then numeric."""
    if 0 <= index < 26:
        return f"candidate_{chr(97 + index)}"
    return f"candidate_{index + 1}"


def _blind_candidates_list(candidates: Any) -> list[dict[str, Any]]:
    """Normalize the candidates input into a list of candidate dicts."""
    if isinstance(candidates, dict):
        raw = candidates.get("candidates", candidates)
    else:
        raw = candidates
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def build_blind_evaluation_pack(suite: dict[str, Any], candidates: Any) -> dict[str, Any]:
    """Build a blind-evaluation pack that hides candidate model/provider identity.

    Assigns each candidate a deterministic blind label (candidate_a, candidate_b,
    ... in input order) and produces an evaluator-facing view that exposes only
    the blind label, case id, and answer text — never provider/model/version. The
    identity mapping is kept in a separate ``reveal_map`` section so results can be
    de-anonymized later via ``reveal_blind_evaluation_results``.

    Deterministic and stdlib only. No model call, no network. Candidate answers are
    read from each candidate's ``answers`` object keyed by suite case id (a missing
    answer becomes an empty string so every case exposes the same blind ids).
    """
    cand_list = _blind_candidates_list(candidates)
    blind_ids: list[str] = []
    reveal_map: dict[str, Any] = {}
    answers_by_label: dict[str, dict[str, str]] = {}
    for idx, cand in enumerate(cand_list):
        label = _blind_label(idx)
        blind_ids.append(label)
        reveal_map[label] = {
            field: cand.get(field) for field in ("provider", "model", "version", "vendor")
            if cand.get(field) is not None
        }
        answers = cand.get("answers") if isinstance(cand.get("answers"), dict) else {}
        answers_by_label[label] = {str(k): str(v or "") for k, v in answers.items()}

    cases_out: list[dict[str, Any]] = []
    for idx, case in enumerate(suite.get("cases", []) or [], start=1):
        if not isinstance(case, dict):
            continue
        cid = case.get("id") or f"case-{idx}"
        cases_out.append({
            "case_id": cid,
            "genre": case.get("genre", ""),
            "prompt_fields": case.get("prompt_fields", {}) if isinstance(case.get("prompt_fields"), dict) else {},
            "candidates": [
                {"blind_id": label, "answer": answers_by_label[label].get(cid, "")}
                for label in blind_ids
            ],
        })

    return {
        "method": "blind_evaluation_pack_v1",
        "boundary": (
            "blind-eval packaging skeleton only; evaluator view hides identity, "
            "labels follow input order (pre-shuffle upstream if positional leakage "
            "matters), and no model is invoked"
        ),
        "metadata": {
            "suite_name": suite.get("metadata", {}).get("name", "") if isinstance(suite.get("metadata"), dict) else "",
            "candidate_count": len(blind_ids),
            "case_count": len(cases_out),
        },
        "evaluator_view": {
            "blind_ids": blind_ids,
            "cases": cases_out,
        },
        "reveal_map": reveal_map,
    }


def validate_blind_evaluation_pack(pack: Any) -> dict[str, Any]:
    """Validate a blind-evaluation pack's shape and identity hygiene.

    Checks: metadata present; evaluator case ids exist and are unique; every case
    exposes the same set of candidate blind ids; and no evaluator-facing candidate
    item leaks an obvious identity field (provider/model/version/vendor/...).
    Deterministic, stdlib only. Returns {passed, errors, warnings, case_count, blind_ids}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(pack, dict):
        return {"passed": False, "errors": [f"pack must be a JSON object, got {type(pack).__name__}"],
                "warnings": [], "case_count": 0, "blind_ids": []}

    if not isinstance(pack.get("metadata"), dict) or not pack["metadata"]:
        errors.append("pack 'metadata' must be a non-empty object")

    view = pack.get("evaluator_view")
    if not isinstance(view, dict):
        errors.append("pack must contain an 'evaluator_view' object")
        return {"passed": not errors, "errors": errors, "warnings": warnings,
                "case_count": 0, "blind_ids": []}

    declared_ids = view.get("blind_ids", [])
    if not isinstance(declared_ids, list) or not declared_ids:
        errors.append("evaluator_view 'blind_ids' must be a non-empty list")
        declared_ids = []
    declared_set = {str(b) for b in declared_ids}

    cases = view.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("evaluator_view must contain a non-empty 'cases' list")
        cases = []

    seen_ids: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        where = f"case #{idx}"
        if not isinstance(case, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = case.get("case_id")
        if not (isinstance(cid, str) and cid.strip()):
            errors.append(f"{where}: 'case_id' must be a non-empty string")
        else:
            where = f"case '{cid}'"
            if cid in seen_ids:
                errors.append(f"{where}: duplicate case_id")
            seen_ids.add(cid)

        cand_items = case.get("candidates")
        if not isinstance(cand_items, list) or not cand_items:
            errors.append(f"{where}: 'candidates' must be a non-empty list")
            continue
        case_blind: set[str] = set()
        for c in cand_items:
            if not isinstance(c, dict):
                errors.append(f"{where}: each candidate must be an object")
                continue
            leaked = sorted(BLIND_IDENTITY_FIELDS & set(c))
            if leaked:
                errors.append(f"{where}: evaluator-facing candidate leaks identity field(s) {leaked}")
            bid = c.get("blind_id")
            if not (isinstance(bid, str) and bid.strip()):
                errors.append(f"{where}: candidate 'blind_id' must be a non-empty string")
            else:
                case_blind.add(bid)
            if "answer" not in c:
                errors.append(f"{where}: candidate '{bid}' is missing 'answer'")
        if declared_set and case_blind != declared_set:
            errors.append(f"{where}: candidate blind ids {sorted(case_blind)} "
                          f"do not match declared blind ids {sorted(declared_set)}")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_count": len(cases),
        "blind_ids": sorted(declared_set),
    }


def reveal_blind_evaluation_results(pack: dict[str, Any],
                                    scores_or_reviews: Any) -> dict[str, Any]:
    """Join reviewer scores back to hidden candidate identity and aggregate.

    ``scores_or_reviews`` maps case id -> {blind_id -> score/review}. A ``scores``
    wrapper key is also accepted. Scores that parse as numbers are aggregated into
    a per-candidate mean; non-numeric reviews are counted but not averaged. Returns
    per-candidate aggregates joined to the reveal_map identity, plus per-case rows.

    Deterministic, stdlib only. No model call, no network.
    """
    reveal_map = pack.get("reveal_map", {}) if isinstance(pack.get("reveal_map"), dict) else {}
    if isinstance(scores_or_reviews, dict) and isinstance(scores_or_reviews.get("scores"), dict):
        scores = scores_or_reviews["scores"]
    elif isinstance(scores_or_reviews, dict):
        scores = scores_or_reviews
    else:
        scores = {}

    numeric_by_label: dict[str, list[float]] = {}
    count_by_label: dict[str, int] = {}
    cases_scored: list[dict[str, Any]] = []

    for case_id, per_case in scores.items():
        if not isinstance(per_case, dict):
            continue
        row: dict[str, Any] = {"case_id": str(case_id), "scores": {}}
        for blind_id, value in per_case.items():
            blind_id = str(blind_id)
            row["scores"][blind_id] = value
            count_by_label[blind_id] = count_by_label.get(blind_id, 0) + 1
            try:
                numeric_by_label.setdefault(blind_id, []).append(float(value))
            except (TypeError, ValueError):
                pass
        cases_scored.append(row)

    all_labels = sorted(set(count_by_label) | set(reveal_map))
    per_candidate: list[dict[str, Any]] = []
    for label in all_labels:
        nums = numeric_by_label.get(label, [])
        per_candidate.append({
            "blind_id": label,
            "identity": reveal_map.get(label, {}),
            "score_count": count_by_label.get(label, 0),
            "numeric_count": len(nums),
            "mean_score": round(sum(nums) / len(nums), 4) if nums else None,
        })

    return {
        "method": "blind_evaluation_reveal_v1",
        "boundary": "joins reviewer scores to hidden identity; aggregates numeric scores only",
        "candidate_count": len(per_candidate),
        "cases_scored": cases_scored,
        "per_candidate": per_candidate,
    }


# --- Stage 5: comparison-baseline matrix + aggregate skeleton v1 --------------

# Supported comparison arm types. "model" arms carry hidden identity; the other
# three are baselines produced offline (human writing, a generic prompt, and the
# project/system prompt). No arm type invokes a model here.
COMPARISON_ARM_TYPES = frozenset({"human", "generic_prompt", "project_prompt", "model"})

# Identity fields kept out of the evaluator-safe arm descriptors.
COMPARISON_IDENTITY_FIELDS = ("provider", "model", "version", "vendor")


def _comparison_arms_list(candidate_outputs: Any) -> list[dict[str, Any]]:
    """Normalize the comparison arms input into a list of arm dicts."""
    if isinstance(candidate_outputs, dict):
        raw = candidate_outputs.get("arms", candidate_outputs.get("candidate_outputs", candidate_outputs))
    else:
        raw = candidate_outputs
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, dict)]


def build_comparison_baseline_matrix(suite: dict[str, Any],
                                     candidate_outputs: Any) -> dict[str, Any]:
    """Build a comparison-baseline matrix joining suite case ids to each arm output.

    Arms represent comparison conditions produced offline — a human writing
    baseline, a generic-prompt baseline, a project/system-prompt baseline, and any
    model/version arms — never invoking a model here. Each arm gets an evaluator-safe
    descriptor (arm_id, arm_type, label, optional blind_id); provider/model/version
    identity is kept in a separate ``identity_map`` section. Every case exposes the
    same arm ids; a missing output is represented deterministically as an empty
    string (validation surfaces it as a warning).

    Deterministic and stdlib only. No model call, no network.
    """
    arms_in = _comparison_arms_list(candidate_outputs)
    arms_out: list[dict[str, Any]] = []
    identity_map: dict[str, Any] = {}
    outputs_by_arm: dict[str, dict[str, str]] = {}
    arm_ids: list[str] = []

    for idx, arm in enumerate(arms_in):
        arm_type = str(arm.get("arm_type") or "").strip()
        arm_id = str(arm.get("arm_id") or "").strip() or f"{arm_type or 'arm'}_{idx + 1}"
        label = str(arm.get("label") or arm_id).strip()
        arm_ids.append(arm_id)
        descriptor = {"arm_id": arm_id, "arm_type": arm_type, "label": label}
        blind_id = arm.get("blind_id")
        if isinstance(blind_id, str) and blind_id.strip():
            descriptor["blind_id"] = blind_id.strip()
        arms_out.append(descriptor)
        identity_map[arm_id] = {
            field: arm.get(field) for field in COMPARISON_IDENTITY_FIELDS
            if arm.get(field) is not None
        }
        outputs = arm.get("outputs") if isinstance(arm.get("outputs"), dict) else {}
        outputs_by_arm[arm_id] = {str(k): str(v or "") for k, v in outputs.items()}

    cases_out: list[dict[str, Any]] = []
    for idx, case in enumerate(suite.get("cases", []) or [], start=1):
        if not isinstance(case, dict):
            continue
        cid = case.get("id") or f"case-{idx}"
        cases_out.append({
            "case_id": cid,
            "genre": case.get("genre", ""),
            "arms": [
                {"arm_id": aid, "output": outputs_by_arm[aid].get(cid, "")}
                for aid in arm_ids
            ],
        })

    return {
        "method": "comparison_baseline_matrix_v1",
        "boundary": (
            "offline comparison matrix skeleton only; arms carry pre-produced "
            "outputs, identity is separated from evaluator-safe fields, and no "
            "model is invoked"
        ),
        "metadata": {
            "suite_name": suite.get("metadata", {}).get("name", "") if isinstance(suite.get("metadata"), dict) else "",
            "arm_count": len(arms_out),
            "case_count": len(cases_out),
            "arm_types": sorted({a["arm_type"] for a in arms_out if a["arm_type"]}),
        },
        "arms": arms_out,
        "identity_map": identity_map,
        "cases": cases_out,
    }


def validate_comparison_baseline_matrix(matrix: Any) -> dict[str, Any]:
    """Validate a comparison-baseline matrix's shape.

    Checks: metadata present; arm descriptors have unique arm ids and supported
    arm_type values; case ids exist and are unique; every case exposes the same
    arm-id set. Missing/empty outputs are reported as warnings (not errors) so a
    partially-populated matrix validates. Deterministic, stdlib only.
    Returns {passed, errors, warnings, case_count, arm_ids, arm_types}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(matrix, dict):
        return {"passed": False, "errors": [f"matrix must be a JSON object, got {type(matrix).__name__}"],
                "warnings": [], "case_count": 0, "arm_ids": [], "arm_types": []}

    if not isinstance(matrix.get("metadata"), dict) or not matrix["metadata"]:
        errors.append("matrix 'metadata' must be a non-empty object")

    arms = matrix.get("arms")
    arm_ids: list[str] = []
    arm_types: set[str] = set()
    if not isinstance(arms, list) or not arms:
        errors.append("matrix must contain a non-empty 'arms' list")
        arms = []
    seen_arm_ids: set[str] = set()
    for idx, arm in enumerate(arms, start=1):
        where = f"arm #{idx}"
        if not isinstance(arm, dict):
            errors.append(f"{where}: must be an object")
            continue
        aid = arm.get("arm_id")
        if not (isinstance(aid, str) and aid.strip()):
            errors.append(f"{where}: 'arm_id' must be a non-empty string")
        else:
            if aid in seen_arm_ids:
                errors.append(f"arm '{aid}': duplicate arm_id")
            seen_arm_ids.add(aid)
            arm_ids.append(aid)
        atype = arm.get("arm_type")
        if not (isinstance(atype, str) and atype.strip()):
            errors.append(f"{where}: 'arm_type' must be a non-empty string")
        elif atype not in COMPARISON_ARM_TYPES:
            errors.append(f"{where}: unsupported arm_type '{atype}' "
                          f"(supported: {sorted(COMPARISON_ARM_TYPES)})")
        else:
            arm_types.add(atype)
        for leak in COMPARISON_IDENTITY_FIELDS:
            if leak in arm:
                errors.append(f"{where}: evaluator-facing arm descriptor leaks identity field '{leak}'")

    declared_arm_set = set(arm_ids)
    cases = matrix.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("matrix must contain a non-empty 'cases' list")
        cases = []
    seen_case_ids: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        where = f"case #{idx}"
        if not isinstance(case, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = case.get("case_id")
        if not (isinstance(cid, str) and cid.strip()):
            errors.append(f"{where}: 'case_id' must be a non-empty string")
        else:
            where = f"case '{cid}'"
            if cid in seen_case_ids:
                errors.append(f"{where}: duplicate case_id")
            seen_case_ids.add(cid)
        case_arms = case.get("arms")
        if not isinstance(case_arms, list) or not case_arms:
            errors.append(f"{where}: 'arms' must be a non-empty list")
            continue
        case_arm_set: set[str] = set()
        for a in case_arms:
            if not isinstance(a, dict):
                errors.append(f"{where}: each arm entry must be an object")
                continue
            aid = a.get("arm_id")
            if isinstance(aid, str) and aid.strip():
                case_arm_set.add(aid)
            if "output" not in a:
                errors.append(f"{where}: arm '{aid}' is missing 'output'")
            elif not str(a.get("output") or "").strip():
                warnings.append(f"{where}: arm '{aid}' has an empty output")
        if declared_arm_set and case_arm_set != declared_arm_set:
            errors.append(f"{where}: arm ids {sorted(case_arm_set)} do not match "
                          f"declared arm ids {sorted(declared_arm_set)}")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "case_count": len(cases),
        "arm_ids": sorted(declared_arm_set),
        "arm_types": sorted(arm_types),
    }


def summarize_comparison_baseline_scores(matrix: dict[str, Any],
                                         scoring_results: Any = None) -> dict[str, Any]:
    """Aggregate per-arm scores from supplied numeric case scores.

    ``scoring_results`` maps case id -> {arm_id -> score}. A ``scores`` wrapper key
    is also accepted, as is the ``cases_scored`` shape emitted by
    ``reveal_blind_evaluation_results`` (case_id + scores keyed by arm/blind id).
    Only values that parse as numbers are aggregated into a per-arm mean; no score
    is ever invented. Arms with no numeric score report mean_score=None.

    Deterministic, stdlib only. No model call, no network.
    """
    arms = matrix.get("arms", []) if isinstance(matrix.get("arms"), list) else []
    identity_map = matrix.get("identity_map", {}) if isinstance(matrix.get("identity_map"), dict) else {}
    arm_descriptors = {a["arm_id"]: a for a in arms if isinstance(a, dict) and a.get("arm_id")}

    scores = {}
    if isinstance(scoring_results, dict):
        if isinstance(scoring_results.get("scores"), dict):
            scores = scoring_results["scores"]
        elif isinstance(scoring_results.get("cases_scored"), list):
            scores = {
                row.get("case_id"): row.get("scores", {})
                for row in scoring_results["cases_scored"]
                if isinstance(row, dict) and isinstance(row.get("scores"), dict)
            }
        else:
            scores = scoring_results

    numeric_by_arm: dict[str, list[float]] = {}
    count_by_arm: dict[str, int] = {}
    for _case_id, per_case in scores.items():
        if not isinstance(per_case, dict):
            continue
        for arm_id, value in per_case.items():
            arm_id = str(arm_id)
            count_by_arm[arm_id] = count_by_arm.get(arm_id, 0) + 1
            try:
                numeric_by_arm.setdefault(arm_id, []).append(float(value))
            except (TypeError, ValueError):
                pass

    all_ids = sorted(set(arm_descriptors) | set(count_by_arm))
    per_arm: list[dict[str, Any]] = []
    for aid in all_ids:
        nums = numeric_by_arm.get(aid, [])
        desc = arm_descriptors.get(aid, {})
        per_arm.append({
            "arm_id": aid,
            "arm_type": desc.get("arm_type", ""),
            "label": desc.get("label", ""),
            "identity": identity_map.get(aid, {}),
            "score_count": count_by_arm.get(aid, 0),
            "numeric_count": len(nums),
            "mean_score": round(sum(nums) / len(nums), 4) if nums else None,
        })

    scored = [row for row in per_arm if row["mean_score"] is not None]
    best = max(scored, key=lambda r: r["mean_score"])["arm_id"] if scored else None
    return {
        "method": "comparison_baseline_summary_v1",
        "boundary": "aggregates supplied numeric scores per arm only; never invents scores; no model invoked",
        "has_scores": bool(scored),
        "arm_count": len(per_arm),
        "per_arm": per_arm,
        "best_arm_id": best,
    }


# --- Stage 5: outcome metrics logging + summary skeleton v1 -------------------


def load_outcome_metrics_log(path: str | Path) -> dict[str, Any]:
    """Load an outcome-metrics log JSON (metadata + rows) into a dict.

    Stdlib only. Raises ValueError on a non-object payload or a missing rows list
    so callers can distinguish a load failure from a validation failure.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        log = json.load(f)
    if not isinstance(log, dict):
        raise ValueError(f"outcome metrics log must be a JSON object, got {type(log).__name__}")
    if not isinstance(log.get("rows"), list):
        raise ValueError("outcome metrics log is missing a 'rows' list")
    return log


def _levenshtein(a: str, b: str) -> int:
    """Deterministic stdlib Levenshtein edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (0 if ca == cb else 1)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def _parse_iso_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-like timestamp via stdlib; return None if unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _outcome_row_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Compute deterministic per-row metrics (adoption/edit distance/duration/rework)."""
    metrics: dict[str, Any] = {
        "adoption_rate": None,
        "edit_distance": None,
        "duration_seconds": None,
        "rework_rounds": None,
    }

    # Adoption: explicit accepted bool, or accepted_sections / total_sections.
    total_sections = row.get("total_sections")
    accepted_sections = row.get("accepted_sections")
    if isinstance(total_sections, (int, float)) and total_sections:
        try:
            metrics["adoption_rate"] = round(float(accepted_sections or 0) / float(total_sections), 4)
        except (TypeError, ValueError):
            metrics["adoption_rate"] = None
    elif isinstance(row.get("accepted"), bool):
        metrics["adoption_rate"] = 1.0 if row["accepted"] else 0.0

    # Edit distance: computed from texts when both supplied, else supplied numeric.
    draft = row.get("draft_text")
    final = row.get("final_text")
    if isinstance(draft, str) and isinstance(final, str):
        metrics["edit_distance"] = _levenshtein(draft, final)
    elif isinstance(row.get("edit_distance"), (int, float)):
        metrics["edit_distance"] = row["edit_distance"]

    # Duration: supplied numeric, else completed_at - started_at.
    if isinstance(row.get("duration_seconds"), (int, float)):
        metrics["duration_seconds"] = round(float(row["duration_seconds"]), 3)
    else:
        started = _parse_iso_timestamp(row.get("started_at"))
        completed = _parse_iso_timestamp(row.get("completed_at"))
        if started and completed:
            metrics["duration_seconds"] = round((completed - started).total_seconds(), 3)

    # Rework rounds: revision_rounds or rework_rounds.
    for key in ("rework_rounds", "revision_rounds"):
        if isinstance(row.get(key), (int, float)):
            metrics["rework_rounds"] = int(row[key])
            break

    return metrics


def validate_outcome_metrics_log(log: Any) -> dict[str, Any]:
    """Validate an outcome-metrics log's shape without any model or network call.

    Schema (v1): {metadata{}, rows[]} where each row has case_id + arm_id (a
    stable identity) plus optional accepted/accepted_sections/total_sections,
    draft_text/final_text or edit_distance, started_at/completed_at or
    duration_seconds, and revision_rounds/rework_rounds.

    Errors: malformed/duplicate identity, non-numeric where numeric required,
    out-of-range values, unparseable timestamps when duration is absent. Warnings:
    missing optional metrics. Deterministic, stdlib only. Returns
    {passed, errors, warnings, row_count, arm_ids}.
    """
    errors: list[str] = []
    warnings: list[str] = []
    arm_ids: set[str] = set()

    if not isinstance(log, dict):
        return {"passed": False, "errors": [f"log must be a JSON object, got {type(log).__name__}"],
                "warnings": [], "row_count": 0, "arm_ids": []}

    rows = log.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("log must contain a non-empty 'rows' list")
        rows = []

    seen_identity: set[tuple[str, str]] = set()
    for idx, row in enumerate(rows, start=1):
        where = f"row #{idx}"
        if not isinstance(row, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = row.get("case_id")
        aid = row.get("arm_id")
        if not (isinstance(cid, str) and cid.strip()):
            errors.append(f"{where}: 'case_id' must be a non-empty string")
            cid = None
        if not (isinstance(aid, str) and aid.strip()):
            errors.append(f"{where}: 'arm_id' must be a non-empty string")
            aid = None
        if cid and aid:
            where = f"row '{cid}/{aid}'"
            key = (cid, aid)
            if key in seen_identity:
                errors.append(f"{where}: duplicate case_id/arm_id identity")
            seen_identity.add(key)
            arm_ids.add(aid)

        # Adoption inputs.
        if "accepted" in row and not isinstance(row["accepted"], bool):
            errors.append(f"{where}: 'accepted' must be a boolean when present")
        for key in ("accepted_sections", "total_sections"):
            if key in row and not isinstance(row[key], (int, float)):
                errors.append(f"{where}: '{key}' must be numeric when present")
        ts = row.get("total_sections")
        if isinstance(ts, (int, float)):
            if ts < 0:
                errors.append(f"{where}: 'total_sections' must be >= 0")
            acc = row.get("accepted_sections")
            if isinstance(acc, (int, float)) and (acc < 0 or acc > ts):
                errors.append(f"{where}: 'accepted_sections' must be within [0, total_sections]")

        # Edit-distance inputs.
        has_texts = isinstance(row.get("draft_text"), str) and isinstance(row.get("final_text"), str)
        if "edit_distance" in row:
            if not isinstance(row["edit_distance"], (int, float)):
                errors.append(f"{where}: 'edit_distance' must be numeric when present")
            elif row["edit_distance"] < 0:
                errors.append(f"{where}: 'edit_distance' must be >= 0")

        # Duration inputs.
        has_duration = isinstance(row.get("duration_seconds"), (int, float))
        if has_duration and row["duration_seconds"] < 0:
            errors.append(f"{where}: 'duration_seconds' must be >= 0")
        started_raw = row.get("started_at")
        completed_raw = row.get("completed_at")
        if not has_duration and (started_raw is not None or completed_raw is not None):
            started = _parse_iso_timestamp(started_raw)
            completed = _parse_iso_timestamp(completed_raw)
            if started is None or completed is None:
                errors.append(f"{where}: 'started_at'/'completed_at' must be ISO-like timestamps "
                              "when 'duration_seconds' is absent")
            elif completed < started:
                errors.append(f"{where}: 'completed_at' must not precede 'started_at'")

        # Rework inputs.
        for key in ("rework_rounds", "revision_rounds"):
            if key in row and not isinstance(row[key], (int, float)):
                errors.append(f"{where}: '{key}' must be numeric when present")
            elif isinstance(row.get(key), (int, float)) and row[key] < 0:
                errors.append(f"{where}: '{key}' must be >= 0")

        # Warn on absent optional metrics (do not fail).
        computed = _outcome_row_metrics(row) if isinstance(row, dict) else {}
        for metric, label in (("adoption_rate", "adoption"), ("edit_distance", "edit distance"),
                              ("duration_seconds", "duration"), ("rework_rounds", "rework rounds")):
            if computed.get(metric) is None:
                warnings.append(f"{where}: no {label} metric available")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "row_count": len(rows),
        "arm_ids": sorted(arm_ids),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarize_outcome_metrics(log: dict[str, Any]) -> dict[str, Any]:
    """Aggregate deterministic outcome metrics per arm and overall.

    For each row computes adoption_rate, edit_distance, duration_seconds and
    rework_rounds (see _outcome_row_metrics), then averages each metric per arm
    and across all rows. Only present metrics contribute to a mean; a metric with
    no data reports None. Deterministic, stdlib only. No model call, no network.
    """
    rows = log.get("rows", []) if isinstance(log.get("rows"), list) else []
    metric_keys = ("adoption_rate", "edit_distance", "duration_seconds", "rework_rounds")

    per_row: list[dict[str, Any]] = []
    by_arm: dict[str, dict[str, list[float]]] = {}
    overall: dict[str, list[float]] = {k: [] for k in metric_keys}

    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        cid = row.get("case_id") or f"case-{idx}"
        aid = row.get("arm_id") or "unknown"
        metrics = _outcome_row_metrics(row)
        per_row.append({"case_id": cid, "arm_id": aid, "metrics": metrics})
        bucket = by_arm.setdefault(aid, {k: [] for k in metric_keys})
        for k in metric_keys:
            if metrics[k] is not None:
                bucket[k].append(float(metrics[k]))
                overall[k].append(float(metrics[k]))

    per_arm = [
        {"arm_id": aid, "row_count": sum(1 for r in per_row if r["arm_id"] == aid),
         "metrics": {k: _mean(buckets[k]) for k in metric_keys}}
        for aid, buckets in sorted(by_arm.items())
    ]

    return {
        "method": "outcome_metrics_summary_v1",
        "boundary": (
            "deterministic aggregation of supplied/derived outcome metrics only; "
            "no model invoked, no persistence, and not a causal or significance analysis"
        ),
        "row_count": len(per_row),
        "per_row": per_row,
        "per_arm": per_arm,
        "overall": {k: _mean(overall[k]) for k in metric_keys},
    }


# --- Stage 5: regression evaluation runner skeleton v1 -----------------------

# Trigger kinds a regression run may declare.
REGRESSION_TRIGGER_KINDS = frozenset({"rules_update", "model_update", "manual"})

# Optional component report kinds a run may aggregate.
REGRESSION_REPORT_KINDS = frozenset({
    "benchmark_scoring", "blind_eval", "comparison_matrix",
    "outcome_metrics", "retrieval_eval",
})


def _regression_reports_list(config: Any) -> list[dict[str, Any]]:
    """Normalize the reports input into a list of report dicts."""
    if isinstance(config, dict):
        raw = config.get("reports", config.get("component_reports", []))
    else:
        raw = config
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def build_regression_evaluation_run(config: Any) -> dict[str, Any]:
    """Assemble a deterministic regression-evaluation run record from a config.

    Captures the trigger (rules_update / model_update / manual), baseline and
    candidate refs, the benchmark suite reference, and any component reports
    (benchmark scoring, blind eval, comparison matrix, outcome metrics, retrieval
    eval). Each report is normalized to {name, kind, status, counts, metrics}
    where metrics may carry baseline/candidate numeric pairs.

    Deterministic and stdlib only. No model call, no network. This assembles and
    normalizes an already-produced set of component reports; it does not execute
    the underlying evaluations.
    """
    cfg = config if isinstance(config, dict) else {}
    trigger = str(cfg.get("trigger_kind") or cfg.get("trigger") or "").strip()

    reports_out: list[dict[str, Any]] = []
    for idx, report in enumerate(_regression_reports_list(cfg), start=1):
        name = str(report.get("name") or report.get("kind") or f"report_{idx}").strip()
        kind = str(report.get("kind") or "").strip()
        status = str(report.get("status") or "").strip()
        counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
        norm_counts = {
            key: int(counts[key]) for key in ("passed", "failed", "warnings")
            if isinstance(counts.get(key), (int, float))
        }
        metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
        norm_metrics: dict[str, Any] = {}
        for mkey, mval in metrics.items():
            if isinstance(mval, dict):
                pair = {
                    side: float(mval[side]) for side in ("baseline", "candidate")
                    if isinstance(mval.get(side), (int, float))
                }
                if pair:
                    norm_metrics[str(mkey)] = pair
            elif isinstance(mval, (int, float)):
                norm_metrics[str(mkey)] = {"candidate": float(mval)}
        reports_out.append({
            "name": name,
            "kind": kind,
            "status": status,
            "counts": norm_counts,
            "metrics": norm_metrics,
        })

    return {
        "method": "regression_evaluation_run_v1",
        "boundary": (
            "local regression-run assembly skeleton only; normalizes already-produced "
            "component reports, does not execute evaluations, invoke a model, or schedule CI"
        ),
        "metadata": {
            "trigger_kind": trigger,
            "baseline_ref": str(cfg.get("baseline_ref") or "").strip(),
            "candidate_ref": str(cfg.get("candidate_ref") or cfg.get("version") or "").strip(),
            "suite": str(cfg.get("suite") or cfg.get("suite_name") or cfg.get("benchmark_suite") or "").strip(),
            "report_count": len(reports_out),
        },
        "reports": reports_out,
    }


def validate_regression_evaluation_run(run: Any) -> dict[str, Any]:
    """Validate a regression-evaluation run record's shape.

    Checks: required metadata (trigger_kind, baseline_ref, candidate_ref, suite);
    supported trigger kind; unique report names; supported report kinds; and
    well-formed numeric metric deltas (baseline/candidate values numeric). Warns
    (not fails) when no reports are present or a report omits status/counts.
    Deterministic, stdlib only. Returns {passed, errors, warnings, report_count, report_names}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(run, dict):
        return {"passed": False, "errors": [f"run must be a JSON object, got {type(run).__name__}"],
                "warnings": [], "report_count": 0, "report_names": []}

    metadata = run.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("run 'metadata' must be an object")
        metadata = {}
    for field in ("trigger_kind", "baseline_ref", "candidate_ref", "suite"):
        if not (isinstance(metadata.get(field), str) and metadata[field].strip()):
            errors.append(f"metadata '{field}' must be a non-empty string")
    trigger = metadata.get("trigger_kind")
    if isinstance(trigger, str) and trigger.strip() and trigger not in REGRESSION_TRIGGER_KINDS:
        errors.append(f"metadata 'trigger_kind' unsupported '{trigger}' "
                      f"(supported: {sorted(REGRESSION_TRIGGER_KINDS)})")

    reports = run.get("reports")
    if not isinstance(reports, list):
        errors.append("run 'reports' must be a list")
        reports = []
    if not reports:
        warnings.append("run has no component reports")

    seen_names: set[str] = set()
    report_names: list[str] = []
    for idx, report in enumerate(reports, start=1):
        where = f"report #{idx}"
        if not isinstance(report, dict):
            errors.append(f"{where}: must be an object")
            continue
        name = report.get("name")
        if not (isinstance(name, str) and name.strip()):
            errors.append(f"{where}: 'name' must be a non-empty string")
        else:
            where = f"report '{name}'"
            if name in seen_names:
                errors.append(f"{where}: duplicate report name")
            seen_names.add(name)
            report_names.append(name)
        kind = report.get("kind")
        if isinstance(kind, str) and kind.strip() and kind not in REGRESSION_REPORT_KINDS:
            warnings.append(f"{where}: unknown report kind '{kind}' "
                            f"(known: {sorted(REGRESSION_REPORT_KINDS)})")
        if not (isinstance(report.get("status"), str) and report["status"].strip()):
            warnings.append(f"{where}: no status declared")
        if not report.get("counts"):
            warnings.append(f"{where}: no counts declared")
        metrics = report.get("metrics", {})
        if metrics and not isinstance(metrics, dict):
            errors.append(f"{where}: 'metrics' must be an object")
        elif isinstance(metrics, dict):
            for mkey, mval in metrics.items():
                if not isinstance(mval, dict):
                    errors.append(f"{where}: metric '{mkey}' must be an object of numeric values")
                    continue
                for side, sval in mval.items():
                    if not isinstance(sval, (int, float)):
                        errors.append(f"{where}: metric '{mkey}.{side}' must be numeric")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "report_count": len(reports),
        "report_names": report_names,
    }


def summarize_regression_evaluation_run(run: dict[str, Any]) -> dict[str, Any]:
    """Summarize a regression-evaluation run into an overall status + deltas.

    Aggregates each report's pass/fail/warn counts, folds in each report's own
    status, and computes candidate-minus-baseline deltas for any metric that
    supplies both sides. Overall status is deterministic:
      - "failed" if any report status is "failed" or any report has failed>0;
      - else "needs_review" if any report status is "needs_review"/"warn", any
        warnings exist, or any metric delta is present;
      - else "passed".
    No model invocation. Stdlib only.
    """
    reports = run.get("reports", []) if isinstance(run.get("reports"), list) else []
    totals = {"passed": 0, "failed": 0, "warnings": 0}
    report_rows: list[dict[str, Any]] = []
    any_failed = False
    any_review = False

    for report in reports:
        if not isinstance(report, dict):
            continue
        counts = report.get("counts", {}) if isinstance(report.get("counts"), dict) else {}
        for key in totals:
            val = counts.get(key)
            if isinstance(val, (int, float)):
                totals[key] += int(val)
        status = str(report.get("status") or "").strip().lower()
        if status == "failed" or (isinstance(counts.get("failed"), (int, float)) and counts["failed"] > 0):
            any_failed = True
        if status in ("needs_review", "warn", "warning"):
            any_review = True

        deltas: dict[str, Any] = {}
        metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
        for mkey, mval in metrics.items():
            if isinstance(mval, dict) and isinstance(mval.get("baseline"), (int, float)) \
                    and isinstance(mval.get("candidate"), (int, float)):
                deltas[str(mkey)] = round(float(mval["candidate"]) - float(mval["baseline"]), 4)
        report_rows.append({
            "name": report.get("name", ""),
            "kind": report.get("kind", ""),
            "status": report.get("status", ""),
            "counts": counts,
            "metric_deltas": deltas,
        })

    has_deltas = any(row["metric_deltas"] for row in report_rows)
    if any_failed:
        overall = "failed"
    elif any_review or totals["warnings"] > 0 or has_deltas:
        overall = "needs_review"
    else:
        overall = "passed"

    return {
        "method": "regression_evaluation_summary_v1",
        "boundary": (
            "deterministic aggregation of supplied component reports only; no model "
            "invoked, no evaluation executed, and no CI scheduling"
        ),
        "trigger_kind": run.get("metadata", {}).get("trigger_kind", "") if isinstance(run.get("metadata"), dict) else "",
        "status": overall,
        "report_count": len(report_rows),
        "totals": totals,
        "reports": report_rows,
    }


def run_retrieval_eval_suite(suite: dict[str, Any], k: int | None = None,
                             db_path: str | Path | None = None,
                             bm25_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a full eval suite end-to-end and return a JSON-serializable report.

    Imports ``suite.corpus`` into an isolated SQLite DB (a throwaway temp file when
    ``db_path`` is not given), resolves chunk markers, runs ``evaluate_retrieval_cases``
    and augments the report with the suite name. Never mutates the caller's DB_PATH
    beyond the call, and removes any temp DB it created.
    """
    global DB_PATH
    if k is None:
        k = int(suite.get("k", 10) or 10)
    original_db_path = DB_PATH
    created_tmp: str | None = None
    if db_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        db_path = tmp.name
        created_tmp = tmp.name
    DB_PATH = Path(db_path)
    try:
        _import_suite_corpus(suite)
        cases = build_suite_cases(suite)
        report = evaluate_retrieval_cases(cases, k=k, bm25_params=bm25_params)
        report["suite"] = suite.get("suite")
        return report
    finally:
        DB_PATH = original_db_path
        if created_tmp:
            Path(created_tmp).unlink(missing_ok=True)


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|(?<=。)\s*\n", text or "") if p.strip()]


def sentence_has_action_guard(sentence: str) -> bool:
    has_owner = bool(re.search(r"(由|责任单位[:：]?|牵头单位[:：]?|各[\u4e00-\u9fa5]{1,12}(局|办|委|中心|街道|部门)|[\u4e00-\u9fa5]{2,12}(局|办|委|中心|处|科))", sentence))
    has_time = bool(re.search(r"(\d{4}年|\d{1,2}月\d{1,2}日|月底|年底|日前|前完成|每周|每月|季度|年度|限期)", sentence))
    has_result = bool(re.search(r"(形成|完成|建立|实现|达到|不少于|覆盖|台账|清单|报告|机制|制度|预案|闭环)", sentence))
    return has_owner and has_time and has_result


def evidence_text(items: list[dict[str, str]]) -> str:
    return "\n".join(" ".join(str(v) for v in item.values()) for item in items)


def _normalize_string_list(raw: Any) -> list[str]:
    values = raw if isinstance(raw, list) else [raw]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _payload_evidence_search_items(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt payload evidence into search-like items without mutating the payload."""
    items: list[dict[str, Any]] = []
    for idx, raw in enumerate(evidence or [], start=1):
        body = str(raw.get("body") or raw.get("content") or raw.get("text") or "")
        title = str(raw.get("title") or "")
        source = str(raw.get("source") or "")
        url = str(raw.get("url") or "")
        chunk_id = f"payload-evidence-{idx}"
        items.append({
            "chunk_id": chunk_id,
            "document_id": f"payload-document-{idx}",
            "title": title,
            "source": source,
            "url": url,
            "body": body,
            "content": " ".join(part for part in (title, source, url, body) if part),
            "hit_reasons": [],
        })
    return items


def build_structured_writing_plan(payload: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Deterministically link outline sections, draft paragraphs, claims, and evidence.

    This is an additive audit surface for later targeted rewrite/review work. It
    does not query the library database, call models, change status/issues, or
    perform semantic entailment.
    """
    genre_key = payload.get("genre", "work_plan")
    genre_rule = analysis.get("genre") or RULES["genres"].get(genre_key, RULES["genres"]["work_plan"])
    sections = list(genre_rule.get("required_sections") or [])
    paragraphs = split_paragraphs(str(payload.get("draft", "") or ""))
    evidence_items = _payload_evidence_search_items(payload.get("evidence", []) or [])

    outline: list[dict[str, Any]] = []
    for section in sections:
        indexes = [idx for idx, paragraph in enumerate(paragraphs, start=1) if section in paragraph]
        outline.append({"section": section, "present": bool(indexes), "paragraph_indexes": indexes})

    paragraph_entries: list[dict[str, Any]] = []
    for idx, paragraph in enumerate(paragraphs, start=1):
        assigned_section = next((section for section in sections if section in paragraph), None)
        evidence_map = map_claim_to_evidence(paragraph, evidence_items)
        required_markers = list(evidence_map.get("required_markers") or [])
        missing_markers = list(evidence_map.get("missing_markers") or [])
        linked_chunk_ids = sorted({
            str(item.get("chunk_id"))
            for item in evidence_map.get("supporting_items", [])
            if item.get("chunk_id")
        })

        warnings: list[str] = []
        if not required_markers:
            status = "no_claim_markers"
        elif missing_markers:
            status = "needs_verification"
            warnings.append("missing_required_markers")
        else:
            status = "supported"

        paragraph_entries.append({
            "index": idx,
            "text": paragraph,
            "section": assigned_section,
            "required_markers": required_markers,
            "evidence_map": evidence_map,
            "status": status,
            "linked_chunk_ids": linked_chunk_ids,
            "warnings": warnings,
        })

    claim_paragraph_count = sum(1 for p in paragraph_entries if p["required_markers"])
    supported_paragraph_count = sum(1 for p in paragraph_entries if p["status"] == "supported")
    needs_verification_count = sum(1 for p in paragraph_entries if p["status"] == "needs_verification")
    missing_section_count = sum(1 for entry in outline if not entry["present"])

    return {
        "method": "structured_writing_plan_v1",
        "genre": genre_key,
        "outline": outline,
        "paragraphs": paragraph_entries,
        "summary": {
            "paragraph_count": len(paragraph_entries),
            "claim_paragraph_count": claim_paragraph_count,
            "supported_paragraph_count": supported_paragraph_count,
            "needs_verification_count": needs_verification_count,
            "missing_section_count": missing_section_count,
        },
    }


# --- Stage 3: deterministic "approved facts only" gate (v1) ------------------
#
# Additive paragraph-level audit. It checks whether each paragraph's required
# claim markers are covered by request-local approved fact sources. This is a
# lexical/marker gate only: no library DB query, no model call, no semantic
# entailment, and no mutation of existing status/issues/score/writing_state.
def _truthy_approval(raw: dict[str, Any]) -> bool:
    return bool(raw.get("approved") or raw.get("review_approved") or raw.get("is_approved"))


def _approved_fact_search_items(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Adapt request-local approved facts into search-like evidence items.

    ``payload["facts"]`` is treated as user-confirmed pre-approved fact text.
    ``payload["approved_facts"]`` may contain strings or objects with id/text/body.
    Evidence items are included only when explicitly approved.
    """
    items: list[dict[str, Any]] = []
    source_counts = {"facts": 0, "approved_facts": 0, "approved_evidence": 0}

    facts_text = str(payload.get("facts", "") or "").strip()
    if facts_text:
        source_counts["facts"] = 1
        items.append({
            "chunk_id": "payload-facts",
            "document_id": "payload-facts",
            "title": "payload facts",
            "source": "payload.facts",
            "url": "",
            "body": facts_text,
            "content": facts_text,
            "hit_reasons": [],
        })

    for idx, raw in enumerate(payload.get("approved_facts", []) or [], start=1):
        if isinstance(raw, dict):
            text = str(raw.get("text") or raw.get("body") or raw.get("content") or "")
            fact_id = str(raw.get("id") or idx)
            title = str(raw.get("title") or f"approved fact {fact_id}")
        else:
            text = str(raw or "")
            fact_id = str(idx)
            title = f"approved fact {fact_id}"
        if not text.strip():
            continue
        source_counts["approved_facts"] += 1
        chunk_id = f"payload-approved-fact-{fact_id}"
        items.append({
            "chunk_id": chunk_id,
            "document_id": chunk_id,
            "title": title,
            "source": "payload.approved_facts",
            "url": "",
            "body": text,
            "content": " ".join(part for part in (title, text) if part),
            "hit_reasons": [],
        })

    approved_evidence = [
        raw for raw in (payload.get("evidence", []) or [])
        if isinstance(raw, dict) and _truthy_approval(raw)
    ]
    source_counts["approved_evidence"] = len(approved_evidence)
    items.extend(_payload_evidence_search_items(approved_evidence))

    return items, source_counts


def build_approved_facts_audit(payload: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Return per-paragraph approved-fact coverage metadata."""
    approved_items, source_counts = _approved_fact_search_items(payload)
    paragraphs = split_paragraphs(str(payload.get("draft", "") or ""))

    paragraph_entries: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        evidence_map = map_claim_to_evidence(paragraph, approved_items)
        required_markers = list(evidence_map.get("required_markers") or [])
        unapproved_markers = list(evidence_map.get("missing_markers") or [])
        approved_fact_ids = sorted({
            str(item.get("chunk_id"))
            for item in evidence_map.get("supporting_items", [])
            if item.get("chunk_id")
        })
        warnings: list[str] = []
        if not required_markers:
            status = "no_claim_markers"
            approved_fact_ids = []
        elif unapproved_markers:
            status = "uses_unapproved_facts"
            warnings.append("unapproved_or_missing_markers")
        else:
            status = "all_facts_approved"

        paragraph_entries.append({
            "index": index,
            "text": paragraph,
            "required_markers": required_markers,
            "used_fact_markers": [m for m in required_markers if m not in unapproved_markers],
            "unapproved_markers": unapproved_markers,
            "approved_fact_ids": approved_fact_ids,
            "status": status,
            "warnings": warnings,
        })

    claim_paragraph_count = sum(1 for p in paragraph_entries if p["required_markers"])
    approved_paragraph_count = sum(1 for p in paragraph_entries if p["status"] == "all_facts_approved")
    unapproved_paragraph_count = sum(1 for p in paragraph_entries if p["status"] == "uses_unapproved_facts")

    return {
        "method": "approved_facts_audit_v1",
        "approval_provided": bool(approved_items),
        "approved_fact_count": len(approved_items),
        "source_counts": source_counts,
        "paragraphs": paragraph_entries,
        "summary": {
            "paragraph_count": len(paragraph_entries),
            "claim_paragraph_count": claim_paragraph_count,
            "approved_paragraph_count": approved_paragraph_count,
            "unapproved_paragraph_count": unapproved_paragraph_count,
        },
    }


def build_unit_template_profile(payload: dict[str, Any], genre_rule: dict[str, Any]) -> dict[str, Any]:
    """Normalize optional unit-template metadata for prompts and audits.

    This is request-local metadata only: no persistence, no model call, and no
    requirement that callers provide a template.
    """
    raw = payload.get("unit_template") or {}
    if not isinstance(raw, dict):
        raw = {}

    preferred_terms = _normalize_string_list(raw.get("preferred_terms", []))
    forbidden_terms = _normalize_string_list(raw.get("forbidden_terms", []))
    required_signature = str(raw.get("required_signature") or "").strip()
    contact = str(raw.get("contact") or "").strip()
    style_notes = str(raw.get("style_notes") or "").strip()
    unit_name = str(raw.get("unit_name") or "").strip()
    enabled = bool(unit_name or preferred_terms or forbidden_terms or required_signature or contact or style_notes)

    return {
        "method": "unit_template_profile_v1",
        "enabled": enabled,
        "genre_name": genre_rule.get("name", ""),
        "unit_name": unit_name,
        "preferred_terms": preferred_terms,
        "forbidden_terms": forbidden_terms,
        "required_signature": required_signature,
        "contact": contact,
        "style_notes": style_notes,
        "summary": {
            "preferred_term_count": len(preferred_terms),
            "forbidden_term_count": len(forbidden_terms),
            "has_required_signature": bool(required_signature),
            "has_contact": bool(contact),
            "has_style_notes": bool(style_notes),
        },
    }


def _forbidden_phrase_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    phrases: list[dict[str, Any]] = []
    for phrase in _normalize_string_list(RULES.get("vague_phrases", [])):
        phrases.append({"phrase": phrase, "source": "global_vague", "severity": "warning"})

    template = payload.get("unit_template") or {}
    if isinstance(template, dict):
        for phrase in _normalize_string_list(template.get("forbidden_terms", [])):
            phrases.append({"phrase": phrase, "source": "unit_template", "severity": "fail"})

    for phrase in _normalize_string_list(payload.get("forbidden_phrases", [])):
        phrases.append({"phrase": phrase, "source": "payload", "severity": "fail"})

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in phrases:
        key = (item["phrase"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_forbidden_expression_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Audit draft paragraphs for configured forbidden expressions.

    Matching is deterministic substring matching. It does not perform semantic
    paraphrase detection, persistence, UI work, or model calls.
    """
    paragraphs = split_paragraphs(str(payload.get("draft", "") or ""))
    phrases = _forbidden_phrase_sources(payload)
    paragraph_entries: list[dict[str, Any]] = []
    total_matches = 0

    for idx, paragraph in enumerate(paragraphs, start=1):
        matches: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in phrases:
            phrase = item["phrase"]
            if phrase and phrase in paragraph:
                key = (phrase, item["source"])
                if key in seen:
                    continue
                seen.add(key)
                matches.append({
                    "phrase": phrase,
                    "source": item["source"],
                    "severity": item["severity"],
                })
        total_matches += len(matches)
        paragraph_entries.append({
            "index": idx,
            "target": f"p{idx}",
            "text": paragraph,
            "matches": matches,
            "status": "has_forbidden_expression" if matches else "clear",
        })

    return {
        "method": "forbidden_expression_audit_v1",
        "enabled": bool(phrases),
        "configured_phrase_count": len(phrases),
        "paragraphs": paragraph_entries,
        "summary": {
            "paragraph_count": len(paragraph_entries),
            "paragraphs_with_matches": sum(1 for p in paragraph_entries if p["matches"]),
            "match_count": total_matches,
        },
    }


def _paragraph_index_from_target(target: Any) -> int | None:
    match = re.fullmatch(r"p(\d+)", str(target or ""))
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def build_targeted_repair_plan(payload: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Build paragraph-scoped repair units for failed or unverified paragraphs.

    This is planning metadata only. It never rewrites text, calls a model, reads
    the library DB, or changes the existing analysis status.
    """
    paragraphs = split_paragraphs(str(payload.get("draft", "") or ""))
    blockers = [issue for issue in analysis.get("issues", []) or [] if issue.get("level") == "blocker"]
    if not paragraphs or blockers:
        return {
            "method": "targeted_repair_plan_v1",
            "can_repair": False,
            "repair_units": [],
            "summary": {
                "paragraph_count": len(paragraphs),
                "repair_unit_count": 0,
                "blocked": bool(blockers),
            },
        }

    issue_map: dict[int, list[dict[str, Any]]] = {}
    for issue in analysis.get("issues", []) or []:
        paragraph_index = _paragraph_index_from_target(issue.get("target"))
        if paragraph_index is None:
            continue
        issue_map.setdefault(paragraph_index, []).append(issue)

    structured_by_index = {
        int(item.get("index")): item
        for item in analysis.get("structured_writing_plan", {}).get("paragraphs", []) or []
        if item.get("index")
    }
    approved_by_index = {
        int(item.get("index")): item
        for item in analysis.get("approved_facts_audit", {}).get("paragraphs", []) or []
        if item.get("index")
    }

    repair_indexes = set(issue_map)
    for idx, item in structured_by_index.items():
        if item.get("status") == "needs_verification":
            repair_indexes.add(idx)
    for idx, item in approved_by_index.items():
        if item.get("status") == "uses_unapproved_facts":
            repair_indexes.add(idx)

    repair_units: list[dict[str, Any]] = []
    for idx in sorted(i for i in repair_indexes if 1 <= i <= len(paragraphs)):
        structured = structured_by_index.get(idx, {})
        approved = approved_by_index.get(idx, {})
        issues = issue_map.get(idx, [])
        issue_codes = sorted({str(issue.get("code")) for issue in issues if issue.get("code")})
        source_targets = sorted({str(issue.get("target")) for issue in issues if issue.get("target")})
        required_markers = sorted(set(str(m) for m in (
            list(structured.get("required_markers") or []) +
            list(approved.get("required_markers") or [])
        )))
        structured_missing = structured.get("evidence_map", {}).get("missing_markers", [])
        missing_markers = sorted(set(str(m) for m in structured_missing if m))
        unapproved_markers = sorted(set(str(m) for m in approved.get("unapproved_markers", []) or [] if m))
        allowed_fact_ids = sorted(set(str(m) for m in approved.get("approved_fact_ids", []) or [] if m))

        reasons = issue_codes[:]
        if missing_markers:
            reasons.append("missing_required_markers")
        if unapproved_markers:
            reasons.append("unapproved_or_missing_markers")
        if not reasons:
            reasons.append("paragraph_needs_verification")

        marker_note = ", ".join(required_markers) if required_markers else "none"
        allowed_note = ", ".join(allowed_fact_ids) if allowed_fact_ids else "none"
        instruction = (
            f"Rewrite only paragraph {idx}. Preserve all unrelated paragraphs. "
            f"Use only approved facts/evidence listed in allowed_fact_ids: {allowed_note}. "
            f"Required claim markers for this paragraph: {marker_note}. "
            f"Fix only these deterministic issues: {', '.join(sorted(set(reasons)))}."
        )

        repair_units.append({
            "paragraph_index": idx,
            "original_text": paragraphs[idx - 1],
            "issue_codes": issue_codes,
            "source_targets": source_targets,
            "required_markers": required_markers,
            "missing_markers": missing_markers,
            "unapproved_markers": unapproved_markers,
            "allowed_fact_ids": allowed_fact_ids,
            "instruction": instruction,
            "locked": False,
            "scope": "paragraph_only",
        })

    return {
        "method": "targeted_repair_plan_v1",
        "can_repair": bool(repair_units),
        "repair_units": repair_units,
        "summary": {
            "paragraph_count": len(paragraphs),
            "repair_unit_count": len(repair_units),
            "blocked": False,
        },
    }


def _locked_index_set(raw: Any) -> set[int]:
    locked: set[int] = set()
    for value in raw or []:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if idx > 0:
            locked.add(idx)
    return locked


def _version_id_for(paragraphs: list[dict[str, Any]], prefix: str = "draft") -> str:
    payload = [
        {"index": p.get("index"), "text": p.get("text", ""), "locked": bool(p.get("locked"))}
        for p in paragraphs
    ]
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


def build_draft_version(payload: dict[str, Any], version_id: str | None = None) -> dict[str, Any]:
    """Build a deterministic paragraph-based draft snapshot.

    This is local metadata only: no persistence, model call, or semantic review.
    """
    locked_indexes = _locked_index_set(payload.get("locked_paragraphs", []) or [])
    paragraph_entries = [
        {"index": idx, "text": text, "locked": idx in locked_indexes}
        for idx, text in enumerate(split_paragraphs(str(payload.get("draft", "") or "")), start=1)
    ]
    return {
        "method": "draft_version_v1",
        "version_id": version_id or _version_id_for(paragraph_entries),
        "paragraph_count": len(paragraph_entries),
        "paragraphs": paragraph_entries,
        "locked_paragraphs": sorted(idx for idx in locked_indexes if idx <= len(paragraph_entries)),
    }


def _version_paragraph_map(version: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in version.get("paragraphs", []) or []:
        try:
            idx = int(raw.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if idx > 0:
            result[idx] = {"index": idx, "text": str(raw.get("text", "") or ""),
                           "locked": bool(raw.get("locked"))}
    return result


def diff_draft_versions(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return a paragraph-scoped diff between two draft versions."""
    previous_map = _version_paragraph_map(previous)
    current_map = _version_paragraph_map(current)
    entries: list[dict[str, Any]] = []
    for idx in sorted(set(previous_map) | set(current_map)):
        old = previous_map.get(idx)
        new = current_map.get(idx)
        if old is None:
            status = "added"
        elif new is None:
            status = "removed"
        elif old["text"] == new["text"] and old["locked"] == new["locked"]:
            status = "unchanged"
        else:
            status = "changed"
        entries.append({
            "index": idx,
            "status": status,
            "previous_text": old["text"] if old else "",
            "current_text": new["text"] if new else "",
            "previous_locked": bool(old["locked"]) if old else False,
            "current_locked": bool(new["locked"]) if new else False,
        })

    counts = Counter(entry["status"] for entry in entries)
    return {
        "method": "draft_version_diff_v1",
        "previous_version_id": previous.get("version_id"),
        "current_version_id": current.get("version_id"),
        "entries": entries,
        "summary": {
            "changed_count": counts.get("changed", 0),
            "added_count": counts.get("added", 0),
            "removed_count": counts.get("removed", 0),
            "unchanged_count": counts.get("unchanged", 0),
            "entry_count": len(entries),
        },
    }


def _normalize_revisions(revisions: Any) -> list[dict[str, Any]]:
    if isinstance(revisions, dict):
        return [{"paragraph_index": key, "text": value} for key, value in revisions.items()]
    if isinstance(revisions, list):
        return [item for item in revisions if isinstance(item, dict)]
    return []


def apply_paragraph_revisions(base_version: dict[str, Any], revisions: Any,
                              locked_indexes: list[int] | None = None) -> dict[str, Any]:
    """Apply paragraph revisions while preserving locked paragraphs."""
    base_paragraphs = [
        {"index": p["index"], "text": p["text"], "locked": p["locked"]}
        for p in _version_paragraph_map(base_version).values()
    ]
    base_paragraphs.sort(key=lambda p: p["index"])
    by_index = {p["index"]: p for p in base_paragraphs}
    locked = {p["index"] for p in base_paragraphs if p["locked"]} | _locked_index_set(locked_indexes or [])

    applied: list[dict[str, Any]] = []
    skipped_locked: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for raw in _normalize_revisions(revisions):
        try:
            idx = int(raw.get("paragraph_index"))
        except (TypeError, ValueError, AttributeError):
            invalid.append({"revision": raw, "reason": "invalid_paragraph_index"})
            continue
        if idx not in by_index:
            invalid.append({"paragraph_index": idx, "reason": "paragraph_not_found"})
            continue
        if "text" not in raw or raw.get("text") is None:
            invalid.append({"paragraph_index": idx, "reason": "missing_text"})
            continue
        text = str(raw.get("text"))
        if idx in locked:
            skipped_locked.append({"paragraph_index": idx, "text": text})
            continue
        by_index[idx]["text"] = text
        applied.append({"paragraph_index": idx})

    for idx in locked:
        if idx in by_index:
            by_index[idx]["locked"] = True
    new_paragraphs = [by_index[idx] for idx in sorted(by_index)]
    new_version = {
        "method": "draft_version_v1",
        "version_id": _version_id_for(new_paragraphs, prefix="draft-revised"),
        "paragraph_count": len(new_paragraphs),
        "paragraphs": new_paragraphs,
        "locked_paragraphs": sorted(idx for idx in locked if idx in by_index),
    }
    return {
        "method": "paragraph_revision_apply_v1",
        "base_version_id": base_version.get("version_id"),
        "version": new_version,
        "applied_revisions": applied,
        "skipped_locked": skipped_locked,
        "invalid_revisions": invalid,
    }


def rollback_draft_version(version: dict[str, Any]) -> dict[str, Any]:
    paragraphs = [p for p in _version_paragraph_map(version).values()]
    paragraphs.sort(key=lambda p: p["index"])
    return {
        "method": "draft_version_rollback_v1",
        "restored_version_id": version.get("version_id"),
        "draft": "\n\n".join(p["text"] for p in paragraphs),
        "paragraph_count": len(paragraphs),
    }


# --- Stage 3: deterministic writing workflow state (v1) ----------------------
#
# An additive, deterministic surface over analyze_payload. It never changes the
# existing status/issues/score; it only summarizes them into a workflow state so
# the UI/callers know what is allowed next. It is NOT semantic review and NOT
# DOCX formatting. Five states, stable codes + Chinese labels:
#   materials_insufficient / 资料不足  -- any blocker; can_generate/export False
#   ready_to_draft         / 可起草    -- no blocker/fail, no draft yet
#   needs_revision         / 待修      -- draft exists AND fail issues present
#   ready_for_review       / 待审      -- draft exists, no blocker/fail, not approved
#   ready_to_export        / 可导出    -- draft exists, no blocker/fail, approved truthy
WRITING_STATE_LABEL = {
    "materials_insufficient": "资料不足",
    "ready_to_draft": "可起草",
    "needs_revision": "待修",
    "ready_for_review": "待审",
    "ready_to_export": "可导出",
}


def build_writing_state(payload: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Stage 3 workflow state derived from an analysis result.

    Additive only: reads the analysis' issues (level blocker/fail/warning) and the
    payload's draft/approval, and returns a JSON-serializable ``writing_state``.
    An approval flag can NEVER override a blocker or a fail -- a bad draft stays
    ``needs_revision``/``materials_insufficient``. This is deterministic workflow
    state, not semantic review or final DOCX formatting.
    """
    issues = analysis.get("issues", []) or []
    blockers = [i for i in issues if i.get("level") == "blocker"]
    failures = [i for i in issues if i.get("level") == "fail"]
    warnings = [i for i in issues if i.get("level") == "warning"]

    draft_present = bool(str(payload.get("draft", "") or "").strip())
    # Explicit human approval only; never inferred. Either key may carry it.
    approved = bool(payload.get("review_approved") or payload.get("approved"))

    # State precedence guarantees approval cannot mask a blocker/fail.
    if blockers:
        state = "materials_insufficient"
    elif failures:
        state = "needs_revision"
    elif not draft_present:
        state = "ready_to_draft"
    elif approved:
        state = "ready_to_export"
    else:
        state = "ready_for_review"

    can_generate = state != "materials_insufficient"
    can_export = state == "ready_to_export"

    required_actions: list[dict[str, Any]] = []
    if blockers:
        for b in blockers:
            required_actions.append({"code": "fill_" + str(b.get("code", "blocker")),
                                     "target": b.get("target"),
                                     "message": "补齐必备要素/事实：" + str(b.get("message", ""))})
    elif failures:
        for f in failures:
            required_actions.append({"code": "fix_" + str(f.get("code", "fail")),
                                     "target": f.get("target"),
                                     "message": "修复失败项后再进入复核：" + str(f.get("message", ""))})
    elif state == "ready_to_draft":
        required_actions.append({"code": "generate_draft", "target": "draft",
                                 "message": "要素齐备，可进行受约束起草生成。"})
    elif state == "ready_for_review":
        required_actions.append({"code": "human_review", "target": "draft",
                                 "message": "草稿通过确定性词面检查，待人工语义复核后标记 review_approved。"})
    elif state == "ready_to_export":
        required_actions.append({"code": "export_docx", "target": "draft",
                                 "message": "已人工批准，可导出。导出仅为结构化基础稿，非正式排版。"})

    return {
        "state": state,
        "label": WRITING_STATE_LABEL[state],
        "can_generate": can_generate,
        "can_export": can_export,
        "draft_present": draft_present,
        "approved": approved,
        "blockers": blockers,
        "failures": failures,
        "warnings": warnings,
        "required_actions": required_actions,
        "method": "deterministic_writing_state_v1",
    }


def analyze_payload(payload: dict[str, Any]) -> dict[str, Any]:
    genre = payload.get("genre", "work_plan")
    meta = payload.get("fields", {}) or {}
    facts = payload.get("facts", "") or ""
    evidence = payload.get("evidence", []) or []
    draft = payload.get("draft", "") or ""
    genre_rule = RULES["genres"].get(genre, RULES["genres"]["work_plan"])

    issues: list[dict[str, Any]] = []
    missing = [name for name in genre_rule["required_fields"] if not str(meta.get(name, "")).strip()]
    for name in missing:
        issues.append({"level": "blocker", "code": "missing_field", "message": f"缺少必填要素：{name}", "target": name})

    if not facts.strip():
        issues.append({"level": "blocker", "code": "missing_facts", "message": "没有输入事实素材，不能直接生成正式稿。", "target": "facts"})

    if not evidence:
        issues.append({"level": "warning", "code": "no_evidence", "message": "未录入政策或事实来源，涉及依据、数据、政策表述时会被拦截。", "target": "evidence"})

    if draft.strip():
        ev_text = evidence_text(evidence)
        for idx, para in enumerate(split_paragraphs(draft), start=1):
            for phrase in RULES["vague_phrases"]:
                if phrase in para and not sentence_has_action_guard(para):
                    issues.append({"level": "fail", "code": "vague_without_guard", "message": f"第 {idx} 段含空泛表述“{phrase}”，但缺少责任主体、时间节点或可验收结果。", "target": f"p{idx}"})
            if re.search(r"《[^》]{3,60}》|\d+(\.\d+)?%|\d{4}年|\d+(万|亿|项|人|次)", para):
                sample = re.sub(r"\s+", "", para[:32])
                if sample and sample not in re.sub(r"\s+", "", ev_text):
                    issues.append({"level": "fail", "code": "unbound_claim", "message": f"第 {idx} 段存在政策、年份或数据表达，但没有与证据台账形成显式绑定。", "target": f"p{idx}"})

        for sec in genre_rule["required_sections"]:
            if sec not in draft:
                issues.append({"level": "warning", "code": "missing_section", "message": f"草稿缺少建议结构：{sec}", "target": sec})

    unit_template_profile = build_unit_template_profile(payload, genre_rule)
    forbidden_expression_audit = build_forbidden_expression_audit(payload)
    forbidden_issue_keys = {
        (issue.get("target"), issue.get("code"), issue.get("phrase"))
        for issue in issues
    }
    for paragraph in forbidden_expression_audit["paragraphs"]:
        target = paragraph["target"]
        for match in paragraph["matches"]:
            if match["source"] == "global_vague" or match["severity"] != "fail":
                continue
            key = (target, "forbidden_expression", match["phrase"])
            if key in forbidden_issue_keys:
                continue
            forbidden_issue_keys.add(key)
            issues.append({
                "level": "fail",
                "code": "forbidden_expression",
                "message": f"第 {paragraph['index']} 段含禁用表达“{match['phrase']}”（来源：{match['source']}）。",
                "target": target,
                "phrase": match["phrase"],
                "source": match["source"],
            })

    score = max(0, 100 - sum(25 if i["level"] == "blocker" else 14 if i["level"] == "fail" else 6 for i in issues))
    status = "blocked" if any(i["level"] == "blocker" for i in issues) else "fail" if any(i["level"] == "fail" for i in issues) else "pass"
    analysis = {"status": status, "score": score, "issues": issues, "missing": missing, "genre": genre_rule}
    analysis["unit_template_profile"] = unit_template_profile
    analysis["forbidden_expression_audit"] = forbidden_expression_audit
    analysis["structured_writing_plan"] = build_structured_writing_plan(payload, analysis)
    # Additive Stage 3 paragraph-based draft snapshot (does not persist state).
    analysis["draft_version"] = build_draft_version(payload)
    # Additive Stage 3 deterministic workflow state (does not change status/issues).
    analysis["writing_state"] = build_writing_state(payload, analysis)
    # Additive Stage 3 approved-facts audit (does not change status/issues).
    analysis["approved_facts_audit"] = build_approved_facts_audit(payload, analysis)
    # Additive Stage 3 paragraph-scoped repair plan (does not rewrite draft).
    analysis["targeted_repair_plan"] = build_targeted_repair_plan(payload, analysis)
    return analysis


def build_prompt(payload: dict[str, Any], analysis: dict[str, Any]) -> str:
    genre = analysis["genre"]
    fields = payload.get("fields", {}) or {}
    evidence = payload.get("evidence", []) or []
    facts = payload.get("facts", "") or ""
    field_lines = "\n".join(f"- {k}: {v}" for k, v in fields.items() if str(v).strip())
    evidence_lines = "\n".join(f"[{i+1}] {e.get('title','')} | {e.get('source','')} | {e.get('url','')}\n{e.get('body','')}" for i, e in enumerate(evidence))
    unit_profile = analysis.get("unit_template_profile") or build_unit_template_profile(payload, genre)
    unit_template_section = ""
    if unit_profile.get("enabled"):
        preferred_terms = ", ".join(unit_profile.get("preferred_terms") or []) or "none"
        forbidden_terms = ", ".join(unit_profile.get("forbidden_terms") or []) or "none"
        unit_template_section = f"""

Unit template constraints:
- unit_name: {unit_profile.get('unit_name') or 'none'}
- preferred_terms: {preferred_terms}
- forbidden_terms: {forbidden_terms}
- required_signature: {unit_profile.get('required_signature') or 'none'}
- contact: {unit_profile.get('contact') or 'none'}
- style_notes: {unit_profile.get('style_notes') or 'none'}"""
    return f"""你是中文机关材料写作助手。必须先核事实、再成文，不得编造政策、文号、数据、会议精神或审批状态。

文种：{genre['name']}
必备结构：{'、'.join(genre['required_sections'])}

硬性写作规则：
1. 每个政策依据、统计数据、年份节点必须能对应证据台账；没有证据只能写成“需核实”，不得写成事实。
2. 涉及部署事项时，必须写清责任主体、完成时限、工作动作、可验收成果。
3. 不得单独使用“加强组织领导、形成工作合力、确保取得实效”等空泛表述；必须落到机制、频次、责任和结果。
4. 语言要像真实机关材料：稳、准、具体，不写营销文案，不写夸张形容。
5. 输出只给正文草稿，不解释规则。

任务要素：
{field_lines or '无'}

事实素材：
{facts or '无'}

证据台账：
{evidence_lines or '无'}
{unit_template_section}
"""


# --- Stage 6: local configuration + offline model option skeleton v1 ----------

# Supported model modes. "offline" and "prompt_only" never touch the network;
# "openai_compatible" is the existing remote OpenAI-compatible path.
MODEL_MODES = ("offline", "prompt_only", "openai_compatible")

# Model modes that are guaranteed to make no network / model call.
OFFLINE_MODEL_MODES = frozenset({"offline", "prompt_only"})


def build_local_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic, safe-by-default local configuration.

    The default mode is "offline": no network and no model call. An optional
    ``overrides`` dict (e.g. a request body) may set ``model_mode`` and a couple
    of local UI preferences. Provider *configuration presence* is reported as a
    boolean derived from the environment via bool(); credential values and .env
    files are never read into the config. Stdlib only.
    """
    raw = overrides if isinstance(overrides, dict) else {}
    mode = str(raw.get("model_mode") or raw.get("mode") or "offline").strip() or "offline"
    if mode not in MODEL_MODES:
        mode = "offline"
    offline = mode in OFFLINE_MODEL_MODES
    # Only the *presence* of provider settings is surfaced, never the values.
    provider_configured = bool(os.getenv("MATERIAL_LLM_BASE_URL")) and bool(os.getenv("MATERIAL_LLM_API_KEY"))
    return {
        "method": "local_config_v1",
        "model_mode": mode,
        "offline": offline,
        "allow_network": not offline,
        "provider_configured": provider_configured,
        "local_placeholder_enabled": bool(raw.get("local_placeholder_enabled", True)),
        "save_draft_locally": bool(raw.get("save_draft_locally", True)),
        "offline_notice": (
            "离线模式：不进行任何网络或模型调用，仅输出严格提示词/本地占位草稿；"
            "不读取 .env 或任何凭据。"
        ),
        "boundary": (
            "local config + offline model option skeleton v1; no bundled local "
            "inference engine, no dependency install, no credential/.env read"
        ),
    }


def validate_local_config(config: Any) -> dict[str, Any]:
    """Validate a local configuration's shape and mode without any I/O.

    Deterministic, stdlib only. Errors on unsupported model_mode or wrong types;
    warns when a non-offline mode is selected but no provider is configured (that
    path will fall back to prompt_only at call time). Returns
    {passed, errors, warnings, model_mode, offline}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(config, dict):
        return {"passed": False, "errors": [f"config must be a JSON object, got {type(config).__name__}"],
                "warnings": [], "model_mode": None, "offline": None}

    mode = config.get("model_mode", config.get("mode"))
    if not (isinstance(mode, str) and mode.strip()):
        errors.append("'model_mode' must be a non-empty string")
        mode = None
    elif mode not in MODEL_MODES:
        errors.append(f"unsupported model_mode '{mode}' (supported: {list(MODEL_MODES)})")
        mode = None

    for key in ("local_placeholder_enabled", "save_draft_locally"):
        if key in config and not isinstance(config[key], bool):
            errors.append(f"'{key}' must be a boolean when present")

    offline = mode in OFFLINE_MODEL_MODES if mode else None
    if mode == "openai_compatible" and not (
        bool(os.getenv("MATERIAL_LLM_BASE_URL")) and bool(os.getenv("MATERIAL_LLM_API_KEY"))
    ):
        warnings.append("model_mode 'openai_compatible' selected but no provider is configured; "
                        "generation will fall back to prompt_only.")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "model_mode": mode,
        "offline": offline,
    }


def build_offline_placeholder_draft(prompt: str) -> str:
    """Deterministic local placeholder 'draft' for offline mode (no model call)."""
    return (
        "【离线模式占位草稿】本系统当前为完全离线模式，未调用任何模型或网络。\n"
        "以下为可复制的严格提示词，请在你自有的本地/离线模型中使用，或改用在线模式：\n\n"
        + prompt
    )


def call_llm(prompt: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce a draft for ``prompt`` honoring the resolved model mode.

    In an offline mode ("offline"/"prompt_only") this makes NO network or model
    call: it returns the strict prompt (and, for "offline", a local placeholder
    draft) with an explicit no-network marker. Only "openai_compatible" mode may
    reach the network, and only when a provider is configured; otherwise it
    degrades to prompt_only. Never reads .env or credentials beyond os.getenv of
    the documented MATERIAL_LLM_* settings.
    """
    cfg = build_local_config(config or {})
    mode = cfg["model_mode"]

    if mode == "offline":
        return {"mode": "offline", "draft": build_offline_placeholder_draft(prompt),
                "prompt": prompt, "network_used": False,
                "notice": cfg["offline_notice"]}
    if mode == "prompt_only":
        return {"mode": "prompt_only", "draft": "", "prompt": prompt, "network_used": False,
                "notice": "仅提示词模式：不进行网络或模型调用，仅输出严格提示词。"}

    # openai_compatible: only mode allowed to use the network.
    base = os.getenv("MATERIAL_LLM_BASE_URL", "").rstrip("/")
    key = os.getenv("MATERIAL_LLM_API_KEY", "")
    model = os.getenv("MATERIAL_LLM_MODEL", "gpt-4.1")
    if not base or not key:
        return {"mode": "prompt_only", "draft": "", "prompt": prompt, "network_used": False,
                "error": "未配置 MATERIAL_LLM_BASE_URL / MATERIAL_LLM_API_KEY。"}
    data = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=data, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return {"mode": "llm", "draft": content, "prompt": prompt, "network_used": True}
    except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        return {"mode": "error", "draft": "", "prompt": prompt, "network_used": True, "error": str(exc)}


# --- Stage 6: RBAC / workspaces / minimum-permission skeleton v1 --------------

# Roles ordered from most to least privileged. Least privilege is the default.
RBAC_ROLES = ("owner", "admin", "editor", "reviewer", "viewer")

# Actions the permission matrix governs.
RBAC_ACTIONS = (
    "read", "generate", "review", "export",
    "manage_library", "manage_users", "manage_config",
)

# Deterministic role -> allowed-actions matrix. A role grants exactly this set;
# there is no implicit inheritance (kept explicit so the policy is auditable).
RBAC_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset(RBAC_ACTIONS),
    "admin": frozenset({"read", "generate", "review", "export",
                        "manage_library", "manage_users", "manage_config"}),
    "editor": frozenset({"read", "generate", "review", "export", "manage_library"}),
    "reviewer": frozenset({"read", "review"}),
    "viewer": frozenset({"read"}),
}

# The default role assigned when none is supplied: least privilege.
RBAC_DEFAULT_ROLE = "viewer"


def build_access_context(user: dict[str, Any] | None = None,
                         workspace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a deterministic access context for the current demo user + workspace.

    There is no auth provider: the caller supplies a demo/current user descriptor
    (id, display_name, role) and a workspace descriptor (id, name). An unknown or
    missing role falls back to the least-privileged default (viewer). The returned
    context lists the exact allowed actions for the role. Stdlib only; reads no
    credentials, session, or .env.
    """
    raw_user = user if isinstance(user, dict) else {}
    raw_ws = workspace if isinstance(workspace, dict) else {}
    role = str(raw_user.get("role") or "").strip()
    if role not in RBAC_PERMISSIONS:
        role = RBAC_DEFAULT_ROLE
    allowed = sorted(RBAC_PERMISSIONS[role])
    return {
        "method": "access_context_v1",
        "auth": "none",
        "boundary": (
            "deterministic RBAC/workspace policy skeleton only; no auth provider, "
            "no password/session, no persistence, and not a production access control"
        ),
        "user": {
            "id": str(raw_user.get("id") or "demo-user"),
            "display_name": str(raw_user.get("display_name") or raw_user.get("name") or "演示用户"),
            "role": role,
        },
        "workspace": {
            "id": str(raw_ws.get("id") or "default"),
            "name": str(raw_ws.get("name") or "默认项目空间"),
        },
        "allowed_actions": allowed,
        "is_demo": True,
    }


def check_permission(context: Any, action: str,
                     resource: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check whether an access context may perform ``action`` on an optional resource.

    Deterministic least-privilege check: the action must be a known action allowed
    for the context's role. When ``resource`` declares a ``workspace_id`` it must
    match the context's workspace (workspace isolation) or access is denied.
    Returns {allowed, reason, action, role, workspace_id}. Stdlib only.
    """
    if not isinstance(context, dict):
        return {"allowed": False, "reason": "invalid_context", "action": action,
                "role": None, "workspace_id": None}
    role = context.get("user", {}).get("role") if isinstance(context.get("user"), dict) else None
    ws_id = context.get("workspace", {}).get("id") if isinstance(context.get("workspace"), dict) else None

    if action not in RBAC_ACTIONS:
        return {"allowed": False, "reason": "unknown_action", "action": action,
                "role": role, "workspace_id": ws_id}
    if role not in RBAC_PERMISSIONS:
        return {"allowed": False, "reason": "unknown_role", "action": action,
                "role": role, "workspace_id": ws_id}

    # Workspace isolation: a resource in another workspace is out of scope.
    if isinstance(resource, dict) and resource.get("workspace_id") is not None:
        if str(resource.get("workspace_id")) != str(ws_id):
            return {"allowed": False, "reason": "workspace_mismatch", "action": action,
                    "role": role, "workspace_id": ws_id}

    if action in RBAC_PERMISSIONS[role]:
        return {"allowed": True, "reason": "granted", "action": action,
                "role": role, "workspace_id": ws_id}
    return {"allowed": False, "reason": "role_not_permitted", "action": action,
            "role": role, "workspace_id": ws_id}


def validate_access_policy(policy_or_context: Any) -> dict[str, Any]:
    """Validate an access context / policy's shape and role/workspace integrity.

    Checks a user role is supported, a workspace id is present, and (if an
    ``allowed_actions`` list is present) it matches the role's canonical matrix
    exactly and contains only known actions. Deterministic, stdlib only.
    Returns {passed, errors, warnings, role, workspace_id}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(policy_or_context, dict):
        return {"passed": False, "errors": [f"policy must be a JSON object, got {type(policy_or_context).__name__}"],
                "warnings": [], "role": None, "workspace_id": None}

    user = policy_or_context.get("user")
    role = None
    if not isinstance(user, dict):
        errors.append("'user' must be an object")
    else:
        role = user.get("role")
        if not (isinstance(role, str) and role.strip()):
            errors.append("user 'role' must be a non-empty string")
            role = None
        elif role not in RBAC_PERMISSIONS:
            errors.append(f"unsupported role '{role}' (supported: {list(RBAC_ROLES)})")
            role = None

    workspace = policy_or_context.get("workspace")
    ws_id = None
    if not isinstance(workspace, dict):
        errors.append("'workspace' must be an object")
    else:
        ws_id = workspace.get("id")
        if not (isinstance(ws_id, str) and ws_id.strip()):
            errors.append("workspace 'id' must be a non-empty string")
            ws_id = None

    allowed = policy_or_context.get("allowed_actions")
    if allowed is not None:
        if not isinstance(allowed, list):
            errors.append("'allowed_actions' must be a list when present")
        else:
            unknown = [a for a in allowed if a not in RBAC_ACTIONS]
            if unknown:
                errors.append(f"allowed_actions contains unknown action(s) {unknown}")
            if role is not None and not unknown and set(allowed) != set(RBAC_PERMISSIONS[role]):
                errors.append(f"allowed_actions do not match the canonical matrix for role '{role}'")

    if role == "viewer":
        warnings.append("role 'viewer' is read-only (least privilege)")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "role": role,
        "workspace_id": ws_id,
    }


# --- Stage 6: governance (encryption metadata / backup / retention / audit) v1 -

# Recognized (metadata-only) encryption algorithm labels and key sources. These
# are descriptors for a governance policy, NOT an encryption implementation.
GOVERNANCE_ENC_ALGORITHMS = frozenset({"none", "aes-256-gcm", "aes-128-gcm", "chacha20-poly1305"})
GOVERNANCE_KEY_SOURCES = frozenset({"none", "local_keyring", "os_keychain", "kms", "operator_supplied"})

# Audit actions and results this skeleton recognizes (open-ended but validated).
GOVERNANCE_AUDIT_RESULTS = frozenset({"success", "denied", "error"})

# Artifact types a retention policy may cover.
GOVERNANCE_ARTIFACT_TYPES = ("draft", "evidence", "library_document", "audit_log", "backup")

# Fields that must never appear on an encryption-policy descriptor (no key values).
GOVERNANCE_FORBIDDEN_KEY_FIELDS = ("key", "key_value", "secret", "password", "private_key", "passphrase")


def build_encryption_policy(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return encryption *policy metadata* — never key material.

    Describes the declared algorithm, at-rest/in-transit status, and an abstract
    key_source label. This is a governance descriptor only; it neither encrypts
    anything nor reads/stores a key value. Stdlib only.
    """
    raw = options if isinstance(options, dict) else {}
    algorithm = str(raw.get("algorithm") or "none").strip().lower()
    if algorithm not in GOVERNANCE_ENC_ALGORITHMS:
        algorithm = "none"
    key_source = str(raw.get("key_source") or "none").strip().lower()
    if key_source not in GOVERNANCE_KEY_SOURCES:
        key_source = "none"
    at_rest = bool(raw.get("at_rest", algorithm != "none"))
    in_transit = bool(raw.get("in_transit", False))
    return {
        "method": "encryption_policy_v1",
        "boundary": (
            "encryption policy metadata only; no cipher is implemented and no key "
            "value is read, stored, or returned"
        ),
        "algorithm": algorithm,
        "key_source": key_source,
        "at_rest_enabled": at_rest,
        "in_transit_enabled": in_transit,
        "status": "declared" if algorithm != "none" else "disabled",
        "note": "此为治理策略元数据，不含任何密钥值，也未实现真实加密。",
    }


def validate_encryption_policy(policy: Any) -> dict[str, Any]:
    """Validate an encryption-policy descriptor and reject any key-material leak."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(policy, dict):
        return {"passed": False, "errors": [f"policy must be a JSON object, got {type(policy).__name__}"],
                "warnings": []}
    leaked = [f for f in GOVERNANCE_FORBIDDEN_KEY_FIELDS if f in policy]
    if leaked:
        errors.append(f"encryption policy must not carry key material field(s) {leaked}")
    algorithm = policy.get("algorithm")
    if not (isinstance(algorithm, str) and algorithm.strip()):
        errors.append("'algorithm' must be a non-empty string")
    elif algorithm not in GOVERNANCE_ENC_ALGORITHMS:
        errors.append(f"unsupported algorithm '{algorithm}' (supported: {sorted(GOVERNANCE_ENC_ALGORITHMS)})")
    key_source = policy.get("key_source")
    if key_source is not None and key_source not in GOVERNANCE_KEY_SOURCES:
        errors.append(f"unsupported key_source '{key_source}' (supported: {sorted(GOVERNANCE_KEY_SOURCES)})")
    if isinstance(algorithm, str) and algorithm == "none":
        warnings.append("algorithm 'none' means no at-rest encryption is declared")
    return {"passed": not errors, "errors": errors, "warnings": warnings}


def _governance_checksum(content: Any) -> str:
    """Deterministic sha256 over provided content/metadata (stdlib only)."""
    if isinstance(content, (dict, list)):
        blob = json.dumps(content, ensure_ascii=False, sort_keys=True)
    else:
        blob = str(content if content is not None else "")
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_backup_manifest(entries: Any, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a deterministic backup manifest over provided logical entries.

    Each entry is {name, kind (file|logical_store), content|checksum, [size]}. When
    ``content`` is provided a deterministic sha256 checksum is computed over it;
    otherwise a supplied ``checksum`` string is kept. No filesystem copy is made —
    this is a manifest/metadata builder only. Stdlib only.
    """
    raw = options if isinstance(options, dict) else {}
    created_at = str(raw.get("created_at") or "").strip() or datetime.now().isoformat(timespec="seconds")
    items: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries if isinstance(entries, list) else [], start=1):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or f"entry-{idx}").strip()
        kind = str(entry.get("kind") or "logical_store").strip()
        if "content" in entry:
            checksum = _governance_checksum(entry.get("content"))
        else:
            checksum = str(entry.get("checksum") or "").strip()
        items.append({"name": name, "kind": kind, "checksum": checksum,
                      "size": entry.get("size") if isinstance(entry.get("size"), int) else None})
    manifest_checksum = _governance_checksum([{"name": i["name"], "checksum": i["checksum"]} for i in items])
    return {
        "method": "backup_manifest_v1",
        "boundary": "manifest/metadata only; no filesystem backup copy is performed",
        "created_at": created_at,
        "entry_count": len(items),
        "entries": items,
        "manifest_checksum": manifest_checksum,
    }


def validate_backup_manifest(manifest: Any) -> dict[str, Any]:
    """Validate a backup manifest's shape and per-entry checksums."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict):
        return {"passed": False, "errors": [f"manifest must be a JSON object, got {type(manifest).__name__}"],
                "warnings": [], "entry_count": 0}
    if not (isinstance(manifest.get("created_at"), str) and manifest["created_at"].strip()):
        errors.append("'created_at' must be a non-empty string")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("manifest must contain a non-empty 'entries' list")
        entries = []
    seen: set[str] = set()
    for idx, entry in enumerate(entries, start=1):
        where = f"entry #{idx}"
        if not isinstance(entry, dict):
            errors.append(f"{where}: must be an object")
            continue
        name = entry.get("name")
        if not (isinstance(name, str) and name.strip()):
            errors.append(f"{where}: 'name' must be a non-empty string")
        elif name in seen:
            errors.append(f"entry '{name}': duplicate name")
        else:
            seen.add(name)
        checksum = entry.get("checksum")
        if not (isinstance(checksum, str) and checksum.startswith("sha256:") and len(checksum) > 12):
            warnings.append(f"{where}: missing or non-sha256 checksum")
    return {"passed": not errors, "errors": errors, "warnings": warnings, "entry_count": len(entries)}


def build_restore_plan(manifest: Any, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive a deterministic restore plan (ordered steps + warnings) from a manifest.

    Produces one restore step per manifest entry plus warnings for entries with a
    missing/weak checksum. This plans a restore; it does NOT execute any restore or
    touch the filesystem. Stdlib only.
    """
    raw = options if isinstance(options, dict) else {}
    dry_run = bool(raw.get("dry_run", True))
    entries = manifest.get("entries", []) if isinstance(manifest, dict) else []
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    for idx, entry in enumerate(entries if isinstance(entries, list) else [], start=1):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or f"entry-{idx}")
        checksum = entry.get("checksum")
        verify = isinstance(checksum, str) and checksum.startswith("sha256:")
        if not verify:
            warnings.append(f"entry '{name}': no verifiable checksum; restore integrity cannot be confirmed")
        steps.append({
            "order": len(steps) + 1,
            "name": name,
            "kind": str(entry.get("kind") or "logical_store"),
            "action": "verify_and_restore" if verify else "restore_unverified",
            "checksum": checksum if isinstance(checksum, str) else "",
        })
    if not steps:
        warnings.append("no restorable entries found in manifest")
    return {
        "method": "restore_plan_v1",
        "boundary": "restore planning only; no restore is executed and no filesystem is modified",
        "dry_run": dry_run,
        "step_count": len(steps),
        "steps": steps,
        "warnings": warnings,
    }


def validate_restore_plan(plan: Any) -> dict[str, Any]:
    """Validate a restore plan's shape (ordered steps with names/actions)."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return {"passed": False, "errors": [f"plan must be a JSON object, got {type(plan).__name__}"],
                "warnings": [], "step_count": 0}
    steps = plan.get("steps")
    if not isinstance(steps, list):
        errors.append("'steps' must be a list")
        steps = []
    expected_order = 1
    for idx, step in enumerate(steps, start=1):
        where = f"step #{idx}"
        if not isinstance(step, dict):
            errors.append(f"{where}: must be an object")
            continue
        if not (isinstance(step.get("name"), str) and step["name"].strip()):
            errors.append(f"{where}: 'name' must be a non-empty string")
        if step.get("action") not in ("verify_and_restore", "restore_unverified"):
            errors.append(f"{where}: unsupported action '{step.get('action')}'")
        if step.get("order") != expected_order:
            errors.append(f"{where}: 'order' must be sequential ({expected_order} expected)")
        expected_order += 1
    return {"passed": not errors, "errors": errors,
            "warnings": list(plan.get("warnings", []) or []), "step_count": len(steps)}


def build_retention_policy(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a retention policy: retention days per artifact type + deletion report.

    ``options`` may set ``days`` (a mapping of artifact type -> int days) and
    ``artifacts`` (a list of {type, id, age_days}); artifacts whose age exceeds the
    retention window are reported as deletion *candidates* (never deleted here).
    Stdlib only; no destructive action.
    """
    raw = options if isinstance(options, dict) else {}
    default_days = {"draft": 90, "evidence": 365, "library_document": 730,
                    "audit_log": 365, "backup": 180}
    raw_days = raw.get("days") if isinstance(raw.get("days"), dict) else {}
    days = {}
    for atype in GOVERNANCE_ARTIFACT_TYPES:
        val = raw_days.get(atype, default_days[atype])
        days[atype] = int(val) if isinstance(val, (int, float)) and val >= 0 else default_days[atype]

    candidates: list[dict[str, Any]] = []
    for idx, art in enumerate(raw.get("artifacts", []) or [], start=1):
        if not isinstance(art, dict):
            continue
        atype = str(art.get("type") or "").strip()
        age = art.get("age_days")
        if atype in days and isinstance(age, (int, float)) and age > days[atype]:
            candidates.append({
                "id": str(art.get("id") or f"artifact-{idx}"),
                "type": atype,
                "age_days": int(age),
                "retention_days": days[atype],
                "over_by_days": int(age) - days[atype],
            })
    return {
        "method": "retention_policy_v1",
        "boundary": "retention policy + deletion-candidate report only; nothing is deleted",
        "retention_days": days,
        "deletion_candidate_count": len(candidates),
        "deletion_candidates": candidates,
    }


def validate_retention_policy(policy: Any) -> dict[str, Any]:
    """Validate a retention policy's shape (non-negative integer day windows)."""
    errors: list[str] = []
    if not isinstance(policy, dict):
        return {"passed": False, "errors": [f"policy must be a JSON object, got {type(policy).__name__}"],
                "warnings": []}
    days = policy.get("retention_days")
    if not isinstance(days, dict) or not days:
        errors.append("'retention_days' must be a non-empty object")
    else:
        for atype, val in days.items():
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                errors.append(f"retention_days['{atype}'] must be a non-negative integer")
    return {"passed": not errors, "errors": errors, "warnings": []}


def build_audit_record(event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a normalized audit-log record from an event descriptor.

    Fields: timestamp, actor, action, workspace_id, resource, result, reason. A
    missing timestamp is filled deterministically at build time. This records an
    event; it does not persist it. Stdlib only; no credential/.env read.
    """
    raw = event if isinstance(event, dict) else {}
    result = str(raw.get("result") or "success").strip()
    if result not in GOVERNANCE_AUDIT_RESULTS:
        result = "success"
    return {
        "method": "audit_record_v1",
        "timestamp": str(raw.get("timestamp") or "").strip() or datetime.now().isoformat(timespec="seconds"),
        "actor": str(raw.get("actor") or "").strip(),
        "action": str(raw.get("action") or "").strip(),
        "workspace_id": str(raw.get("workspace_id") or "").strip(),
        "resource": str(raw.get("resource") or "").strip(),
        "result": result,
        "reason": str(raw.get("reason") or "").strip(),
    }


def validate_audit_record(record: Any) -> dict[str, Any]:
    """Validate an audit record: actor and action required; result must be known."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return {"passed": False, "errors": [f"record must be a JSON object, got {type(record).__name__}"],
                "warnings": []}
    for field in ("actor", "action"):
        if not (isinstance(record.get(field), str) and record[field].strip()):
            errors.append(f"'{field}' must be a non-empty string")
    if not (isinstance(record.get("timestamp"), str) and record["timestamp"].strip()):
        errors.append("'timestamp' must be a non-empty string")
    result = record.get("result")
    if result is not None and result not in GOVERNANCE_AUDIT_RESULTS:
        errors.append(f"unsupported result '{result}' (supported: {sorted(GOVERNANCE_AUDIT_RESULTS)})")
    if not (isinstance(record.get("workspace_id"), str) and record.get("workspace_id", "").strip()):
        warnings.append("no workspace_id on audit record")
    leaked = [f for f in GOVERNANCE_FORBIDDEN_KEY_FIELDS if f in record]
    if leaked:
        errors.append(f"audit record must not carry secret field(s) {leaked}")
    return {"passed": not errors, "errors": errors, "warnings": warnings}


def build_governance_policy() -> dict[str, Any]:
    """Assemble the deterministic default governance policy summary (metadata only)."""
    return {
        "method": "governance_policy_v1",
        "boundary": (
            "governance metadata/manifest/audit skeleton only; no real encryption, "
            "no destructive delete, no backup copy/restore execution, no credential/.env read"
        ),
        "encryption": build_encryption_policy({}),
        "retention": build_retention_policy({}),
        "audit_results": sorted(GOVERNANCE_AUDIT_RESULTS),
        "artifact_types": list(GOVERNANCE_ARTIFACT_TYPES),
    }


def add_evidence(item: dict[str, str]) -> dict[str, str]:
    item_id = str(uuid.uuid4())
    title = item.get("title", "").strip()
    source = item.get("source", "").strip()
    url = item.get("url", "").strip()
    body = item.get("body", "").strip()
    now = datetime.now().isoformat(timespec="seconds")
    conn = db()
    conn.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)", (item_id, title, source, url, body, now))
    conn.execute("INSERT INTO evidence_fts VALUES (?, ?, ?, ?)", (item_id, title, source, body))
    conn.commit()
    conn.close()
    return {"id": item_id, "title": title, "source": source, "url": url, "body": body, "created_at": now}


def search_evidence(q: str) -> list[dict[str, str]]:
    conn = db()
    rows = conn.execute(
        "SELECT e.id,e.title,e.source,e.url,e.body,e.created_at FROM evidence_fts f JOIN evidence e ON e.id=f.id WHERE evidence_fts MATCH ? LIMIT 20",
        (q or "*",),
    ).fetchall()
    conn.close()
    return [dict(zip(["id", "title", "source", "url", "body", "created_at"], row)) for row in rows]


def _bounded_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def build_docx_style_profile(payload_or_options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic DOCX style defaults inspired by GB/T 9704-2012.

    This is a local export profile, not a certification that the resulting file
    fully satisfies the national standard. It uses stdlib-only OOXML generation.
    """
    raw = payload_or_options or {}
    if not isinstance(raw, dict):
        raw = {}
    options = raw.get("style_profile") if isinstance(raw.get("style_profile"), dict) else raw
    if not isinstance(options, dict):
        options = {}

    default_margins = {
        "top": 2126,
        "right": 1984,
        "bottom": 1984,
        "left": 1984,
        "header": 851,
        "footer": 992,
        "gutter": 0,
    }
    raw_margins = options.get("margins_twips") or options.get("margins") or {}
    if not isinstance(raw_margins, dict):
        raw_margins = {}
    margins = {
        key: _bounded_int(raw_margins.get(key), default, 0, 4320)
        for key, default in default_margins.items()
    }

    font_family = str(options.get("font_family") or options.get("body_font") or "FangSong").strip() or "FangSong"
    title_font = str(options.get("title_font") or "SimHei").strip() or "SimHei"
    heading_font = str(options.get("heading_font") or "SimHei").strip() or "SimHei"
    latin_font = str(options.get("latin_font") or "Times New Roman").strip() or "Times New Roman"

    return {
        "method": "docx_style_profile_v1",
        "standard": "GB/T 9704-2012-inspired",
        "boundary": "deterministic OOXML style profile only; not full formal layout certification",
        "page": {
            "size": "A4",
            "width_twips": 11906,
            "height_twips": 16838,
            "margins_twips": margins,
        },
        "fonts": {
            "body": font_family,
            "title": title_font,
            "heading": heading_font,
            "latin": latin_font,
        },
        "paragraph": {
            "first_line_indent_twips": _bounded_int(options.get("first_line_indent_twips"), 640, 0, 1440),
            "line_spacing_twips": _bounded_int(options.get("line_spacing_twips"), 560, 240, 960),
            "body_alignment": str(options.get("body_alignment") or "both"),
            "title_alignment": str(options.get("title_alignment") or "center"),
        },
        "font_size_half_points": {
            "title": _bounded_int(options.get("title_font_size_half_points"), 44, 18, 72),
            "heading": _bounded_int(options.get("heading_font_size_half_points"), 32, 18, 56),
            "body": _bounded_int(options.get("body_font_size_half_points"), 32, 18, 56),
        },
        "style_ids": {
            "title": "MaterialTitle",
            "heading": "MaterialHeading",
            "body": "MaterialBody",
        },
        "footer": {
            "page_number": bool(options.get("page_number", True)),
            "alignment": str(options.get("footer_alignment") or "center"),
        },
    }


def _docx_rfonts(profile: dict[str, Any], role: str) -> str:
    fonts = profile["fonts"]
    east_asia = escape(str(fonts.get(role) or fonts["body"]))
    latin = escape(str(fonts.get("latin") or "Times New Roman"))
    return f'<w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{east_asia}"/>'


def docx_style_xml(profile: dict[str, Any]) -> str:
    style_ids = profile["style_ids"]
    sizes = profile["font_size_half_points"]
    paragraph = profile["paragraph"]
    body_id = escape(str(style_ids["body"]))
    title_id = escape(str(style_ids["title"]))
    heading_id = escape(str(style_ids["heading"]))
    body_size = str(sizes["body"])
    title_size = str(sizes["title"])
    heading_size = str(sizes["heading"])
    first_line = str(paragraph["first_line_indent_twips"])
    line_spacing = str(paragraph["line_spacing_twips"])
    body_alignment = escape(str(paragraph["body_alignment"]))
    title_alignment = escape(str(paragraph["title_alignment"]))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="{title_id}">
    <w:name w:val="Material Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="{title_alignment}"/><w:spacing w:after="240"/></w:pPr>
    <w:rPr>{_docx_rfonts(profile, "title")}<w:b/><w:sz w:val="{title_size}"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="{heading_id}">
    <w:name w:val="Material Heading"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:spacing w:before="240" w:after="120" w:line="{line_spacing}" w:lineRule="exact"/></w:pPr>
    <w:rPr>{_docx_rfonts(profile, "heading")}<w:b/><w:sz w:val="{heading_size}"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="{body_id}">
    <w:name w:val="Material Body"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="{body_alignment}"/><w:ind w:firstLine="{first_line}"/><w:spacing w:line="{line_spacing}" w:lineRule="exact"/></w:pPr>
    <w:rPr>{_docx_rfonts(profile, "body")}<w:sz w:val="{body_size}"/></w:rPr>
  </w:style>
</w:styles>'''


def _is_docx_heading(text: str) -> bool:
    stripped = text.strip()
    return bool(re.match(r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\d+[.．、])\S+", stripped))


def _first_nonempty_string(raw: Any) -> str:
    if isinstance(raw, list):
        return "\n".join(str(item or "").strip() for item in raw if str(item or "").strip())
    return str(raw or "").strip()


def build_docx_layout_plan(title: str, body: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build deterministic paragraph roles for stdlib DOCX export.

    This maps plain text to simple export roles only. It does not infer legal
    document semantics, paginate, or certify a formal GB/T layout.
    """
    raw_options = options or {}
    if not isinstance(raw_options, dict):
        raw_options = {}
    style_profile = raw_options.get("style_profile") if isinstance(raw_options.get("style_profile"), dict) else {}
    signature = _first_nonempty_string(raw_options.get("signature") or style_profile.get("signature"))
    imprint_raw = raw_options.get("imprint") if raw_options.get("imprint") is not None else style_profile.get("imprint")
    imprint = _first_nonempty_string(imprint_raw)

    paragraphs: list[dict[str, Any]] = []
    clean_title = str(title or "").strip()
    if clean_title:
        paragraphs.append({"index": len(paragraphs) + 1, "role": "title", "text": clean_title})
    for paragraph in split_paragraphs(body):
        role = "heading" if _is_docx_heading(paragraph) else "body"
        paragraphs.append({"index": len(paragraphs) + 1, "role": role, "text": paragraph})
    if signature:
        paragraphs.append({"index": len(paragraphs) + 1, "role": "signature", "text": signature})
    if imprint:
        paragraphs.append({"index": len(paragraphs) + 1, "role": "imprint", "text": imprint})

    counts = Counter(entry["role"] for entry in paragraphs)
    return {
        "method": "docx_layout_plan_v1",
        "paragraphs": paragraphs,
        "signature_enabled": bool(signature),
        "imprint_enabled": bool(imprint),
        "summary": {
            "paragraph_count": len(paragraphs),
            "title_count": counts.get("title", 0),
            "heading_count": counts.get("heading", 0),
            "body_count": counts.get("body", 0),
            "signature_count": counts.get("signature", 0),
            "imprint_count": counts.get("imprint", 0),
        },
    }


def _structured_text(raw: Any) -> str:
    return str(raw or "").strip()


def _structured_list(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []


def build_docx_structured_fields(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize request-local structured DOCX export fields.

    This produces deterministic metadata only. It does not persist templates or
    attempt full official-document layout validation.
    """
    raw = options or {}
    if not isinstance(raw, dict):
        raw = {}
    fields = raw.get("style_profile") if isinstance(raw.get("style_profile"), dict) else raw
    if not isinstance(fields, dict):
        fields = {}

    attachments: list[dict[str, Any]] = []
    for idx, item in enumerate(_structured_list(fields.get("attachments")), start=1):
        if isinstance(item, dict):
            title = _structured_text(item.get("title") or item.get("name") or item.get("text"))
        else:
            title = _structured_text(item)
        if title:
            attachments.append({"index": len(attachments) + 1, "title": title})

    tables: list[dict[str, Any]] = []
    for raw_table in _structured_list(fields.get("tables")):
        if not isinstance(raw_table, dict):
            continue
        headers = [_structured_text(h) for h in _structured_list(raw_table.get("headers"))]
        headers = [h for h in headers if h]
        rows: list[list[str]] = []
        for raw_row in _structured_list(raw_table.get("rows")):
            if isinstance(raw_row, dict):
                if headers:
                    row = [_structured_text(raw_row.get(header)) for header in headers]
                else:
                    row = [_structured_text(v) for v in raw_row.values()]
            elif isinstance(raw_row, list):
                row = [_structured_text(v) for v in raw_row]
            else:
                continue
            if any(row):
                rows.append(row)
        if headers or rows:
            tables.append({
                "index": len(tables) + 1,
                "title": _structured_text(raw_table.get("title") or raw_table.get("name")),
                "headers": headers,
                "rows": rows,
            })

    return {
        "method": "docx_structured_fields_v1",
        "document_number": _structured_text(fields.get("document_number")),
        "issuer": _structured_text(fields.get("issuer")),
        "recipient": _structured_text(fields.get("recipient")),
        "attachments": attachments,
        "tables": tables,
        "summary": {
            "has_document_number": bool(_structured_text(fields.get("document_number"))),
            "has_issuer": bool(_structured_text(fields.get("issuer"))),
            "has_recipient": bool(_structured_text(fields.get("recipient"))),
            "attachment_count": len(attachments),
            "table_count": len(tables),
        },
    }


# Conservative built-in list of fonts commonly bundled with Chinese Windows /
# Office installs. Membership here means "very likely to render on a standard
# GB/T official-document workstation", not "the only valid font". Unknown fonts
# are still exported; they only trigger an advisory preflight warning.
_KNOWN_DOCX_FONTS = frozenset({
    # East Asian body/title/heading faces
    "FangSong", "仿宋", "仿宋_GB2312",
    "SimSun", "宋体", "NSimSun", "新宋体",
    "SimHei", "黑体",
    "KaiTi", "楷体", "楷体_GB2312",
    "Microsoft YaHei", "微软雅黑",
    "DengXian", "等线",
    "STSong", "华文宋体", "STKaiti", "华文楷体", "STFangsong", "华文仿宋",
    "STHeiti", "华文黑体", "STZhongsong", "华文中宋",
    # Latin faces
    "Times New Roman", "Arial", "Calibri", "Cambria", "Courier New", "Georgia",
})

# Deterministic fallback chains keyed by role. The first entry is the most
# faithful stand-in; later entries are progressively safer defaults. These are
# advisory suggestions for a downstream renderer, not automatic substitutions.
_DOCX_FONT_FALLBACKS = {
    "body": ["FangSong", "仿宋", "SimSun", "宋体"],
    "title": ["SimHei", "黑体", "SimSun", "宋体"],
    "heading": ["SimHei", "黑体", "SimSun", "宋体"],
    "latin": ["Times New Roman", "Cambria", "Georgia", "Arial"],
}


def build_font_fallback_plan(style_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a deterministic DOCX font fallback / known-font plan.

    For each font role (body/title/heading/latin) it reports the requested font,
    whether that font is in a conservative built-in known list, and a fallback
    chain of safer candidates. Unknown requested fonts produce advisory warnings.

    This is metadata only: it never rewrites the exported DOCX fonts, download
    fonts, or inspect the host system. It is stdlib-only and request-local.
    """
    profile = build_docx_style_profile(style_profile or {})
    fonts = profile["fonts"]

    roles: list[dict[str, Any]] = []
    warnings: list[str] = []
    for role in ("body", "title", "heading", "latin"):
        requested = str(fonts.get(role) or fonts.get("body") or "").strip()
        is_known = requested in _KNOWN_DOCX_FONTS
        # Fallback candidates: the role's chain plus a universal SimSun/宋体 tail,
        # excluding the requested font itself and de-duplicated while preserving order.
        raw_candidates = list(_DOCX_FONT_FALLBACKS.get(role, [])) + ["SimSun", "宋体"]
        candidates: list[str] = []
        for candidate in raw_candidates:
            if candidate != requested and candidate not in candidates:
                candidates.append(candidate)
        if not is_known and requested:
            warnings.append(
                f"字体“{requested}”（{role}）不在保守内置已知字体列表中，"
                f"导出仍会写入该字体，但目标机器可能缺失，建议核对或改用已知字体。"
            )
        roles.append({
            "role": role,
            "requested": requested,
            "is_known": is_known,
            "fallback_candidates": candidates,
        })

    known_count = sum(1 for r in roles if r["is_known"])
    return {
        "method": "docx_font_fallback_plan_v1",
        "boundary": (
            "advisory font fallback metadata only; does not substitute, embed, "
            "or download fonts, and does not read host-installed fonts"
        ),
        "known_font_list_size": len(_KNOWN_DOCX_FONTS),
        "roles": roles,
        "warnings": warnings,
        "summary": {
            "role_count": len(roles),
            "known_count": known_count,
            "unknown_count": len(roles) - known_count,
            "warning_count": len(warnings),
        },
    }


def build_export_preflight_report(
    title: str,
    body: str,
    style_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic pre-export preflight report for DOCX generation.

    Aggregates the font fallback plan, layout plan summary, and structured field
    summary, and lists export boundary warnings so a caller can inspect what the
    stdlib OOXML export will and will not do before writing a file.

    This inspects only request-local inputs. It performs no file write, no model
    call, and no network access.
    """
    font_plan = build_font_fallback_plan(style_profile or {})
    layout_plan = build_docx_layout_plan(title, body, {"style_profile": style_profile or {}})
    structured_fields = build_docx_structured_fields(style_profile or {})

    boundary_warnings = [
        "stdlib OOXML 导出，不是完整 GB/T 9704-2012 正式排版认证。",
        "不做真实分页、版记定位或字体嵌入/替换，字体仅按请求写入。",
        "表格仅支持表头/行，无合并单元格、列宽或复杂样式。",
    ]
    boundary_warnings.extend(font_plan["warnings"])

    return {
        "method": "docx_export_preflight_v1",
        "version": "docx_export_preflight_v1",
        "font_fallback_plan": font_plan,
        "layout_plan_summary": layout_plan["summary"],
        "structured_field_summary": structured_fields["summary"],
        "export_boundary_warnings": boundary_warnings,
        "summary": {
            "font_role_count": font_plan["summary"]["role_count"],
            "unknown_font_count": font_plan["summary"]["unknown_count"],
            "paragraph_count": layout_plan["summary"]["paragraph_count"],
            "table_count": structured_fields["summary"]["table_count"],
            "attachment_count": structured_fields["summary"]["attachment_count"],
            "boundary_warning_count": len(boundary_warnings),
        },
    }


def _docx_paragraph(text: str, style_id: str) -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{escape(style_id)}"/></w:pPr><w:r><w:t>{escape(text)}</w:t></w:r></w:p>'


def _docx_table_cell(text: str, style_id: str) -> str:
    return f'<w:tc><w:p><w:pPr><w:pStyle w:val="{escape(style_id)}"/></w:pPr><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:tc>'


def docx_table_xml(table: dict[str, Any], style_id: str) -> str:
    rows: list[list[str]] = []
    headers = [str(h) for h in table.get("headers", []) or []]
    if headers:
        rows.append(headers)
    rows.extend([[str(cell) for cell in row] for row in table.get("rows", []) or []])
    body = "".join(
        "<w:tr>" + "".join(_docx_table_cell(cell, style_id) for cell in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>{body}</w:tbl>"


def docx_footer_xml(profile: dict[str, Any], layout_plan: dict[str, Any]) -> str:
    alignment = escape(str(profile.get("footer", {}).get("alignment") or "center"))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr><w:jc w:val="{alignment}"/></w:pPr>
    <w:r><w:fldChar w:fldCharType="begin"/></w:r>
    <w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
</w:ftr>'''


def export_docx(title: str, body: str, style_profile: dict[str, Any] | None = None) -> bytes:
    profile = build_docx_style_profile(style_profile or {})
    style_ids = profile["style_ids"]
    margins = profile["page"]["margins_twips"]
    layout_plan = build_docx_layout_plan(title, body, {"style_profile": style_profile or {}})
    structured_fields = build_docx_structured_fields(style_profile or {})
    role_styles = {
        "title": style_ids["title"],
        "heading": style_ids["heading"],
        "body": style_ids["body"],
        "signature": style_ids["body"],
        "imprint": style_ids["body"],
    }
    document_parts: list[str] = []
    body_tail_added = False

    def append_structured_tail() -> None:
        nonlocal body_tail_added
        if body_tail_added:
            return
        for attachment in structured_fields["attachments"]:
            document_parts.append(_docx_paragraph(f"附件{attachment['index']}：{attachment['title']}", style_ids["body"]))
        for table in structured_fields["tables"]:
            if table.get("title"):
                document_parts.append(_docx_paragraph(str(table["title"]), style_ids["body"]))
            document_parts.append(docx_table_xml(table, style_ids["body"]))
        body_tail_added = True

    for entry in layout_plan["paragraphs"]:
        role = entry["role"]
        if role in {"signature", "imprint"}:
            append_structured_tail()
        document_parts.append(_docx_paragraph(entry["text"], role_styles.get(role, style_ids["body"])))
        if role == "title":
            if structured_fields["document_number"]:
                document_parts.append(_docx_paragraph(structured_fields["document_number"], style_ids["body"]))
            if structured_fields["issuer"]:
                document_parts.append(_docx_paragraph(f"签发人：{structured_fields['issuer']}", style_ids["body"]))
            if structured_fields["recipient"]:
                document_parts.append(_docx_paragraph(f"主送机关：{structured_fields['recipient']}", style_ids["body"]))
    append_structured_tail()
    document = "".join(document_parts)
    content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    rel_entries = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    footer_ref = ""
    footer_enabled = bool(profile.get("footer", {}).get("page_number"))
    if footer_enabled:
        content_types = content_types.replace(
            "</Types>",
            '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>',
        )
        rel_entries.append('<Relationship Id="rIdFooter1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
        footer_ref = '<w:footerReference w:type="default" r:id="rIdFooter1"/>'
    doc_rels = f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rel_entries)}</Relationships>'
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>{document}<w:sectPr>{footer_ref}<w:pgSz w:w="{profile["page"]["width_twips"]}" w:h="{profile["page"]["height_twips"]}"/><w:pgMar w:top="{margins["top"]}" w:right="{margins["right"]}" w:bottom="{margins["bottom"]}" w:left="{margins["left"]}" w:header="{margins["header"]}" w:footer="{margins["footer"]}" w:gutter="{margins["gutter"]}"/></w:sectPr></w:body></w:document>'
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", docx_style_xml(profile))
        if footer_enabled:
            z.writestr("word/footer1.xml", docx_footer_xml(profile, layout_plan))
    return buf.getvalue()


def inspect_docx_package_layout(raw_docx: bytes) -> dict[str, Any]:
    """Inspect a generated DOCX byte package for structural layout invariants.

    Deterministic, stdlib-only. Opens the OOXML zip and reports which parts are
    present, which paragraph style ids the document references, and the page
    size / margin values declared in the section properties. This is a package
    and markup inspector, not a visual renderer: it does not rasterize or
    verify on-screen appearance.
    """
    result: dict[str, Any] = {
        "method": "docx_package_layout_inspector_v1",
        "boundary": (
            "OOXML package/markup inspection only; not a visual renderer and not "
            "a screenshot or pixel-level layout verification"
        ),
        "readable_zip": False,
        "parts": [],
        "has_document": False,
        "has_styles": False,
        "has_footer": False,
        "footer_has_page_field": False,
        "style_references": [],
        "page": {"width_twips": None, "height_twips": None, "has_margins": False, "margins_twips": {}},
    }
    try:
        with zipfile.ZipFile(io.BytesIO(raw_docx)) as z:
            names = z.namelist()
            result["readable_zip"] = True
            result["parts"] = sorted(names)
            result["has_document"] = "word/document.xml" in names
            result["has_styles"] = "word/styles.xml" in names
            result["has_footer"] = "word/footer1.xml" in names
            document = z.read("word/document.xml").decode("utf-8") if result["has_document"] else ""
            footer = z.read("word/footer1.xml").decode("utf-8") if result["has_footer"] else ""
    except (zipfile.BadZipFile, KeyError, OSError, UnicodeDecodeError):
        return result

    # Distinct pStyle references, in first-seen order (deterministic).
    seen: list[str] = []
    for match in re.findall(r'<w:pStyle w:val="([^"]+)"/>', document):
        if match not in seen:
            seen.append(match)
    result["style_references"] = seen

    size_match = re.search(r'<w:pgSz w:w="(\d+)" w:h="(\d+)"/>', document)
    if size_match:
        result["page"]["width_twips"] = int(size_match.group(1))
        result["page"]["height_twips"] = int(size_match.group(2))
    margin_match = re.search(
        r'<w:pgMar w:top="(-?\d+)" w:right="(-?\d+)" w:bottom="(-?\d+)" '
        r'w:left="(-?\d+)" w:header="(-?\d+)" w:footer="(-?\d+)" w:gutter="(-?\d+)"/>',
        document,
    )
    if margin_match:
        keys = ("top", "right", "bottom", "left", "header", "footer", "gutter")
        result["page"]["margins_twips"] = {k: int(v) for k, v in zip(keys, margin_match.groups())}
        result["page"]["has_margins"] = True

    result["footer_has_page_field"] = bool(footer) and "PAGE" in footer
    return result


def build_docx_layout_regression_report(
    title: str,
    body: str,
    style_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic DOCX render/layout regression report.

    Exports a DOCX in-memory, inspects its package layout, and cross-checks the
    result against the declared style profile and the existing preflight helpers
    (table / attachment / unknown-font counts). Emits an ordered list of named
    pass/fail checks plus a rollup summary so a regression suite can assert on
    stable structural invariants without a real renderer.

    Boundary: structural/markup regression only. Visual screenshot and pixel
    layout regression remain future work — no renderer is bundled or invoked.
    """
    profile = build_docx_style_profile(style_profile or {})
    style_ids = profile["style_ids"]
    footer_enabled = bool(profile.get("footer", {}).get("page_number"))

    layout_plan = build_docx_layout_plan(title, body, {"style_profile": style_profile or {}})
    structured_fields = build_docx_structured_fields(style_profile or {})
    font_plan = build_font_fallback_plan(style_profile or {})

    raw = export_docx(title, body, style_profile or {})
    inspection = inspect_docx_package_layout(raw)

    roles_present = {entry["role"] for entry in layout_plan["paragraphs"]}
    refs = set(inspection["style_references"])
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    add_check("zip_readable", inspection["readable_zip"], "package opens as a valid zip")
    add_check("document_present", inspection["has_document"], "word/document.xml exists")
    add_check("styles_present", inspection["has_styles"], "word/styles.xml exists")
    add_check(
        "footer_matches_page_number",
        inspection["has_footer"] == footer_enabled,
        f"footer part present={inspection['has_footer']}, page_number={footer_enabled}",
    )
    if footer_enabled:
        add_check("footer_has_page_field", inspection["footer_has_page_field"],
                  "footer declares a PAGE field")
    for role in ("title", "heading", "body"):
        if role in roles_present:
            add_check(f"{role}_style_referenced", style_ids[role] in refs,
                      f"style id {style_ids[role]} referenced for role {role}")
    add_check(
        "page_size_present",
        inspection["page"]["width_twips"] == profile["page"]["width_twips"]
        and inspection["page"]["height_twips"] == profile["page"]["height_twips"],
        "pgSz width/height match style profile",
    )
    add_check("margins_present", inspection["page"]["has_margins"],
              "pgMar margins declared in section properties")

    summary = {
        "check_count": len(checks),
        "passed_count": sum(1 for c in checks if c["passed"]),
        "failed_count": sum(1 for c in checks if not c["passed"]),
        "table_count": structured_fields["summary"]["table_count"],
        "attachment_count": structured_fields["summary"]["attachment_count"],
        "unknown_font_count": font_plan["summary"]["unknown_count"],
        "paragraph_count": layout_plan["summary"]["paragraph_count"],
    }
    return {
        "method": "docx_layout_regression_v1",
        "boundary": (
            "deterministic structural/markup regression over the generated OOXML "
            "package; visual screenshot and pixel layout regression remain future work"
        ),
        "passed": summary["failed_count"] == 0,
        "package_size_bytes": len(raw),
        "inspection": inspection,
        "checks": checks,
        "failed_checks": [c["name"] for c in checks if not c["passed"]],
        "summary": summary,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def json_response(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        if self.path.startswith("/api/health"):
            self.json_response({"ok": True, "provider_configured": bool(os.getenv("MATERIAL_LLM_API_KEY")), "rules": RULES["genres"]})
            return
        if self.path.startswith("/api/config"):
            self.json_response(build_local_config({}))
            return
        if self.path.startswith("/api/access/context"):
            # No auth provider: expose a deterministic demo context. A role query
            # param lets the demo UI preview a different role's allowed actions.
            role = self._query_param("role")
            self.json_response(build_access_context({"role": role} if role else None))
            return
        if self.path.startswith("/api/governance/policy"):
            self.json_response(build_governance_policy())
            return
        if self.path.startswith("/api/evidence/search"):
            q = self.path.split("q=", 1)[1] if "q=" in self.path else ""
            self.json_response({"items": search_evidence(q)})
            return
        if self.path.startswith("/api/library/documents"):
            self.json_response({"items": list_documents(
                source_type=self._query_param("source_type"),
                region=self._query_param("region"),
                min_authority=self._query_param("min_authority"),
                sort=self._query_param("sort"),
            )})
            return
        if self.path.startswith("/api/library/document"):
            doc_id = self._query_param("id")
            doc = get_document(doc_id)
            if doc is None:
                self.json_response({"error": "document not found"}, 404)
            else:
                self.json_response(doc)
            return
        if self.path.startswith("/api/library/chunks"):
            self.json_response({"items": list_chunks(
                self._query_param("document_id"), sort=self._query_param("sort"))})
            return
        if self.path.startswith("/api/library/jobs"):
            self.json_response({"items": list_jobs(self._query_param("status"))})
            return
        if self.path.startswith("/api/library/search"):
            filters = {k: self._query_param(k) for k in ("source_type", "region", "organization", "format", "min_authority", "status", "document_status", "effective_only", "date_from", "date_to")}
            self.json_response(search_library(
                self._query_param("q"), filters=filters,
                limit=int(self._query_param("limit") or 10),
                vector_config=_vector_config_from_param(self._query_param("vector")),
                rerank_config=_rerank_config_from_param(self._query_param("rerank"))))
            return
        return super().do_GET()

    def _query_param(self, name: str) -> str:
        query = urllib.parse.urlparse(self.path).query
        return urllib.parse.parse_qs(query).get(name, [""])[0]

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/api/analyze":
                self.json_response(analyze_payload(payload))
            elif self.path == "/api/generate":
                analysis = analyze_payload(payload)
                prompt = build_prompt(payload, analysis)
                if analysis["status"] == "blocked":
                    # writing_state is nested in analysis; also surface it top-level.
                    self.json_response({"analysis": analysis, "mode": "blocked", "prompt": prompt,
                                        "draft": "", "writing_state": analysis["writing_state"]})
                else:
                    result = call_llm(prompt, payload.get("config"))
                    if result.get("draft"):
                        payload["draft"] = result["draft"]
                    result["analysis"] = analyze_payload(payload)
                    # Surface the (re-analyzed) workflow state top-level for prompt_only/llm/error.
                    result["writing_state"] = result["analysis"]["writing_state"]
                    self.json_response(result)
            elif self.path == "/api/config/validate":
                report = validate_local_config(payload)
                self.json_response(report, 200 if report["passed"] else HTTPStatus.UNPROCESSABLE_ENTITY)
            elif self.path == "/api/access/validate":
                report = validate_access_policy(payload)
                self.json_response(report, 200 if report["passed"] else HTTPStatus.UNPROCESSABLE_ENTITY)
            elif self.path == "/api/access/check":
                context = payload.get("context") or build_access_context(payload.get("user"), payload.get("workspace"))
                self.json_response(check_permission(context, str(payload.get("action", "")), payload.get("resource")))
            elif self.path == "/api/governance/audit/validate":
                report = validate_audit_record(payload)
                self.json_response(report, 200 if report["passed"] else HTTPStatus.UNPROCESSABLE_ENTITY)
            elif self.path == "/api/evidence":
                self.json_response(add_evidence(payload), HTTPStatus.CREATED)
            elif self.path == "/api/library/import":
                result = import_document(payload)
                ok = result.get("status") in ("succeeded", "duplicate", "new_version")
                self.json_response(result, HTTPStatus.CREATED if ok else HTTPStatus.UNPROCESSABLE_ENTITY)
            elif self.path == "/api/library/update":
                result = update_document(payload)
                if result.get("status") == "error":
                    self.json_response(result, HTTPStatus.NOT_FOUND)
                else:
                    self.json_response(result)
            elif self.path == "/api/library/verify-claim":
                filters = payload.get("filters", {}) or {}
                self.json_response(verify_claim(str(payload.get("claim", "")), filters=filters, limit=int(payload.get("limit", 5) or 5)))
            elif self.path == "/api/library/evaluate-retrieval":
                self.json_response(evaluate_retrieval_cases(payload.get("cases", []) or [], k=int(payload.get("k", 10) or 10)))
            elif self.path == "/api/export/docx":
                raw = export_docx(payload.get("title", "材料草稿"), payload.get("body", ""),
                                  payload.get("style_profile"))
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                self.send_header("Content-Disposition", "attachment; filename=material-draft.docx")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                self.json_response({"error": "not found"}, 404)
        except Exception as exc:
            self.json_response({"error": str(exc)}, 500)


# --- Phase 2B: eval-retrieval CLI (deterministic quality gate) ----------------

# Default acceptance thresholds match the anonymized placeholder suite's fixed
# regression targets. A real suite can pass stricter/looser values on the CLI.
EVAL_DEFAULT_MIN_TITLE_RECALL = 0.8
EVAL_DEFAULT_MIN_CHUNK_RECALL = 1.0
EVAL_DEFAULT_MAX_MISSES = 2

# BM25 sweep grid (used only with --sweep-bm25); kept small and deterministic.
BM25_SWEEP_K1 = [0.9, 1.2, 1.5, 1.8]
BM25_SWEEP_B = [0.5, 0.75, 1.0]


def _eval_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Compact metric summary of a full eval report."""
    return {
        "suite": report.get("suite"),
        "k": report.get("k"),
        "case_count": report.get("case_count"),
        "miss_count": report.get("miss_count"),
        "title_recall_at_k": report.get("title_recall_at_k"),
        "title_mrr": report.get("title_mrr"),
        "chunk_recall_at_k": report.get("chunk_recall_at_k"),
        "chunk_mrr": report.get("chunk_mrr"),
        "bm25": report.get("bm25"),
    }


def _eval_passes(report: dict[str, Any], min_title: float, min_chunk: float,
                 max_misses: int) -> tuple[bool, list[str]]:
    """Check a report against thresholds; return (passed, list-of-failure-reasons)."""
    failures = []
    tr = report.get("title_recall_at_k", 0.0)
    cr = report.get("chunk_recall_at_k", 0.0)
    mc = report.get("miss_count", 0)
    if tr < min_title:
        failures.append(f"title_recall_at_k {tr:.4f} < min {min_title}")
    if cr < min_chunk:
        failures.append(f"chunk_recall_at_k {cr:.4f} < min {min_chunk}")
    if mc > max_misses:
        failures.append(f"miss_count {mc} > max {max_misses}")
    return (not failures), failures


def eval_retrieval_cli(argv: list[str]) -> int:
    """Run the eval suite as a quality gate. Returns a process exit code.

    Prints the report JSON to stdout (and optionally to --output). Exit code is 0
    only when the report meets all thresholds; otherwise non-zero (the JSON is
    still emitted so CI can inspect it).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="eval-retrieval",
        description="Run the anonymized retrieval eval suite as a deterministic quality gate.")
    parser.add_argument("--suite", required=True, help="path to the eval suite JSON")
    parser.add_argument("--k", type=int, default=None, help="Top K (default: suite.k or 10)")
    parser.add_argument("--output", default=None, help="also write the JSON report to this path")
    parser.add_argument("--min-title-recall", type=float, default=EVAL_DEFAULT_MIN_TITLE_RECALL)
    parser.add_argument("--min-chunk-recall", type=float, default=EVAL_DEFAULT_MIN_CHUNK_RECALL)
    parser.add_argument("--max-misses", type=int, default=EVAL_DEFAULT_MAX_MISSES)
    parser.add_argument("--bm25-k1", type=float, default=None, help="override BM25 k1")
    parser.add_argument("--bm25-b", type=float, default=None, help="override BM25 b")
    parser.add_argument("--sweep-bm25", action="store_true",
                        help="sweep a small BM25 k1/b grid and report each plus the best")
    args = parser.parse_args(argv)

    try:
        suite = load_retrieval_eval_suite(args.suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"error": f"failed to load suite: {exc}", "suite_path": args.suite}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    bm25_params = None
    if args.bm25_k1 is not None or args.bm25_b is not None:
        bm25_params = {}
        if args.bm25_k1 is not None:
            bm25_params["k1"] = args.bm25_k1
        if args.bm25_b is not None:
            bm25_params["b"] = args.bm25_b

    try:
        if args.sweep_bm25:
            sweep = []
            for k1 in BM25_SWEEP_K1:
                for b in BM25_SWEEP_B:
                    rep = run_retrieval_eval_suite(suite, k=args.k, bm25_params={"k1": k1, "b": b})
                    passed, failures = _eval_passes(rep, args.min_title_recall,
                                                    args.min_chunk_recall, args.max_misses)
                    entry = _eval_summary(rep)
                    entry["passed"] = passed
                    sweep.append(entry)
            # Best: highest title recall, then chunk recall, then fewest misses.
            best = max(sweep, key=lambda e: (e["title_recall_at_k"], e["chunk_recall_at_k"],
                                             -e["miss_count"]))
            report = {"mode": "sweep", "suite": suite.get("suite"),
                      "grid": {"k1": BM25_SWEEP_K1, "b": BM25_SWEEP_B},
                      "results": sweep, "best": best}
            passed = best["passed"]
            failures = [] if passed else ["no BM25 (k1,b) in the swept grid met all thresholds"]
        else:
            report = run_retrieval_eval_suite(suite, k=args.k, bm25_params=bm25_params)
            passed, failures = _eval_passes(report, args.min_title_recall,
                                            args.min_chunk_recall, args.max_misses)
            report["mode"] = "single"
        report["gate"] = {
            "passed": passed,
            "failures": failures,
            "thresholds": {
                "min_title_recall": args.min_title_recall,
                "min_chunk_recall": args.min_chunk_recall,
                "max_misses": args.max_misses,
            },
        }
    except (ValueError, KeyError) as exc:
        payload = {"error": f"eval run failed: {exc}", "suite": suite.get("suite")}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "eval-retrieval":
        sys.exit(eval_retrieval_cli(sys.argv[2:]))
    db().close()
    port = int(os.getenv("MATERIAL_PORT", "8765"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving http://127.0.0.1:{port}", flush=True)
    httpd.serve_forever()
