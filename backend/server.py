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

    score = max(0, 100 - sum(25 if i["level"] == "blocker" else 14 if i["level"] == "fail" else 6 for i in issues))
    status = "blocked" if any(i["level"] == "blocker" for i in issues) else "fail" if any(i["level"] == "fail" for i in issues) else "pass"
    analysis = {"status": status, "score": score, "issues": issues, "missing": missing, "genre": genre_rule}
    analysis["structured_writing_plan"] = build_structured_writing_plan(payload, analysis)
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
"""


def call_llm(prompt: str) -> dict[str, Any]:
    base = os.getenv("MATERIAL_LLM_BASE_URL", "").rstrip("/")
    key = os.getenv("MATERIAL_LLM_API_KEY", "")
    model = os.getenv("MATERIAL_LLM_MODEL", "gpt-4.1")
    if not base or not key:
        return {"mode": "prompt_only", "draft": "", "prompt": prompt, "error": "未配置 MATERIAL_LLM_BASE_URL / MATERIAL_LLM_API_KEY。"}
    data = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=data, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return {"mode": "llm", "draft": content, "prompt": prompt}
    except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        return {"mode": "error", "draft": "", "prompt": prompt, "error": str(exc)}


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


def export_docx(title: str, body: str) -> bytes:
    paras = [title] + split_paragraphs(body)
    document = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paras)
    content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{document}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


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
                    result = call_llm(prompt)
                    if result.get("draft"):
                        payload["draft"] = result["draft"]
                    result["analysis"] = analyze_payload(payload)
                    # Surface the (re-analyzed) workflow state top-level for prompt_only/llm/error.
                    result["writing_state"] = result["analysis"]["writing_state"]
                    self.json_response(result)
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
                raw = export_docx(payload.get("title", "材料草稿"), payload.get("body", ""))
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
