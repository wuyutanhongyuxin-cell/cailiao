from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
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
        "d.authority_level", "d.region", "d.version",
    ]
    names = [
        "chunk_id", "document_id", "chunk_index", "char_start", "char_end", "chunk_status",
        "content", "location_kind", "location_value", "document_title", "source_url",
        "organization", "document_number", "publish_date", "document_status", "source_type",
        "authority_level", "region", "version",
    ]
    where = []
    params: list[Any] = []
    if filters.get("source_type"):
        where.append("d.source_type=?")
        params.append(normalize_source_type(filters["source_type"]))
    if filters.get("region"):
        where.append("d.region=?")
        params.append(filters["region"])
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


def search_library(query: str, filters: dict[str, str] | None = None, limit: int = 10) -> dict[str, Any]:
    """Deterministic Phase 2A retrieval. No vector model is used."""
    filters = filters or {}
    query = (query or "").strip()
    limit = max(1, min(int(limit or 10), 50))
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

    channels = {
        "lexical_exact": _rank_channel(rows, lexical),
        "fts_or_ngram": _rank_channel(rows, ngram),
    }
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
    return {
        "query": query,
        "items": results[:limit],
        "channels": {name: len(vals) for name, vals in channels.items()},
        "vector": {"enabled": False, "reason": "Phase 2A has no embeddings/vector retrieval"},
    }


def verify_claim(claim: str, filters: dict[str, str] | None = None, limit: int = 5) -> dict[str, Any]:
    """Conservative lexical claim support check over retrieved chunks."""
    result = search_library(claim, filters=filters, limit=limit)
    items = result["items"]
    markers = _required_claim_markers(claim)
    combined = "\n".join(item.get("content") or "" for item in items)
    missing = [m for m in markers if m not in combined]
    if not items:
        status = "unsupported"
        reasons = ["no_retrieved_evidence"]
    elif missing:
        status = "needs_verification"
        reasons = ["required_markers_missing:" + ",".join(missing)]
    else:
        claim_tokens = set(tokenize_query(claim))
        evidence_tokens = set(tokenize_query(combined))
        overlap = claim_tokens & evidence_tokens
        if markers and overlap:
            status = "supported"
            reasons = ["required_markers_present", "lexical_overlap"]
        elif len(overlap) >= max(2, min(5, len(claim_tokens))):
            status = "needs_verification"
            reasons = ["lexical_overlap_without_required_markers"]
        else:
            status = "unsupported"
            reasons = ["insufficient_lexical_overlap"]
    return {
        "claim": claim,
        "status": status,
        "required_markers": markers,
        "missing_markers": missing,
        "cited_chunk_ids": [item["chunk_id"] for item in items[:limit]],
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


def evaluate_retrieval_cases(cases: list[dict[str, Any]], k: int = 10) -> dict[str, Any]:
    """Run a deterministic retrieval benchmark over library search.

    Phase 2B needs a stable benchmark harness before BM25 tuning, embeddings, or
    reranking. Cases name relevant document titles or chunk ids; the evaluator
    reports both title-level and chunk-level metrics so early anonymous suites
    can start coarse and later become more precise.
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
        result = search_library(query, filters=filters, limit=k)
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

        evaluated.append({
            "id": case.get("id") or f"case-{idx}",
            "query": query,
            "top_titles": ranked_titles[:k],
            "top_chunk_ids": ranked_chunks[:k],
            "relevant_titles": sorted(relevant_titles),
            "relevant_chunk_ids": sorted(relevant_chunks),
            "title_recall_at_k": title_recall,
            "chunk_recall_at_k": chunk_recall,
            "hit": bool(
                (relevant_titles and set(ranked_titles[:k]) & relevant_titles) or
                (relevant_chunks and set(ranked_chunks[:k]) & relevant_chunks)
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
        "misses": [{"id": c["id"], "query": c["query"]} for c in misses],
        "cases": evaluated,
        "vector": {"enabled": False, "reason": "Phase 2B benchmark harness only; embeddings are not implemented"},
    }


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|(?<=。)\s*\n", text or "") if p.strip()]


def sentence_has_action_guard(sentence: str) -> bool:
    has_owner = bool(re.search(r"(由|责任单位[:：]?|牵头单位[:：]?|各[\u4e00-\u9fa5]{1,12}(局|办|委|中心|街道|部门)|[\u4e00-\u9fa5]{2,12}(局|办|委|中心|处|科))", sentence))
    has_time = bool(re.search(r"(\d{4}年|\d{1,2}月\d{1,2}日|月底|年底|日前|前完成|每周|每月|季度|年度|限期)", sentence))
    has_result = bool(re.search(r"(形成|完成|建立|实现|达到|不少于|覆盖|台账|清单|报告|机制|制度|预案|闭环)", sentence))
    return has_owner and has_time and has_result


def evidence_text(items: list[dict[str, str]]) -> str:
    return "\n".join(" ".join(str(v) for v in item.values()) for item in items)


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
    return {"status": status, "score": score, "issues": issues, "missing": missing, "genre": genre_rule}


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
            filters = {k: self._query_param(k) for k in ("source_type", "region", "min_authority", "status", "document_status", "effective_only", "date_from", "date_to")}
            self.json_response(search_library(self._query_param("q"), filters=filters, limit=int(self._query_param("limit") or 10)))
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
                    self.json_response({"analysis": analysis, "mode": "blocked", "prompt": prompt, "draft": ""})
                else:
                    result = call_llm(prompt)
                    if result.get("draft"):
                        payload["draft"] = result["draft"]
                    result["analysis"] = analyze_payload(payload)
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


if __name__ == "__main__":
    db().close()
    port = int(os.getenv("MATERIAL_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
