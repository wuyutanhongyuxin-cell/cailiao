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
DOC_STATUS_VALUES = {"effective", "revised", "repealed", "expired", "draft", "unknown"}
DOC_STATUS_ALIASES = {
    "有效": "effective", "现行有效": "effective", "现行": "effective",
    "已修订": "revised", "修订": "revised",
    "已废止": "repealed", "废止": "repealed",
    "已失效": "expired", "失效": "expired",
    "征求意见": "draft", "草案": "draft",
    "未知": "unknown", "": "unknown",
}
# Statuses that make a document non-recommendable by default.
NON_CITABLE_STATUS = {"repealed", "expired"}

CHUNK_STATUS_VALUES = {"citable", "reference_only", "prohibited"}

# Import job lifecycle states.
JOB_STATUS_VALUES = {"succeeded", "duplicate", "failed", "quarantined"}

SUPPORTED_FORMATS = {"txt", "html", "docx"}
# Formats we knowingly cannot parse in this phase and must not silently accept.
QUARANTINE_FORMATS = {"pdf", "xlsx", "xls", "doc"}


class ImportError_(Exception):
    """Base class for evidence-library import failures."""

    reason_code = "import_error"


class ParseError(ImportError_):
    reason_code = "parse_error"


class QuarantineError(ImportError_):
    """Raised for formats we refuse to parse (e.g. PDF) so nothing is silently accepted."""

    reason_code = "unsupported_format"


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
            imported_at TEXT
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
            created_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON evidence_chunks(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON import_jobs(status)")
    conn.commit()


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


def extract_txt(raw: bytes) -> str:
    return _decode_bytes(raw)


def extract_html(raw: bytes) -> str:
    parser = _TextHTMLParser()
    try:
        parser.feed(_decode_bytes(raw))
    except Exception as exc:  # pragma: no cover - defensive
        raise ParseError(f"HTML 解析失败：{exc}") from exc
    return parser.text()


def extract_docx(raw: bytes) -> str:
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
    paragraphs: list[str] = []
    for para in root.iter(f"{ns}p"):
        texts = [node.text or "" for node in para.iter(f"{ns}t")]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)


def normalize_content(text: str) -> str:
    """Normalize newlines/whitespace so chunk offsets stay stable across imports."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def extract_content(fmt: str, raw: bytes) -> str:
    fmt = (fmt or "").lower().lstrip(".")
    if fmt in QUARANTINE_FORMATS:
        raise QuarantineError(f"{fmt.upper()} 暂不支持解析，已隔离，需转换为 TXT/HTML/DOCX 后重试。")
    if fmt == "txt":
        text = extract_txt(raw)
    elif fmt in ("html", "htm"):
        text = extract_html(raw)
    elif fmt == "docx":
        text = extract_docx(raw)
    else:
        raise QuarantineError(f"未知格式“{fmt}”，已隔离，未静默入库。")
    content = normalize_content(text)
    if not content:
        raise ParseError("解析后正文为空，可能是空文件或不受支持的内部结构。")
    return content


CHUNK_MAX_CHARS = 600


def chunk_content(content: str, base_status: str) -> list[dict[str, Any]]:
    """Split into paragraph-based chunks with stable char offsets into `content`."""
    chunks: list[dict[str, Any]] = []
    index = 0
    for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", content):
        para = match.group(0)
        para_start = match.start()
        # Sub-split overly long paragraphs while preserving offsets.
        pos = 0
        while pos < len(para):
            piece = para[pos:pos + CHUNK_MAX_CHARS]
            start = para_start + pos
            chunks.append({
                "chunk_index": index,
                "char_start": start,
                "char_end": start + len(piece),
                "status": base_status,
                "content": piece,
            })
            index += 1
            pos += CHUNK_MAX_CHARS
    return chunks


def default_chunk_status(doc_status: str) -> str:
    """Repealed/expired documents default their chunks to prohibited."""
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
        override_chunk_status = payload.get("chunk_status")
        if override_chunk_status not in CHUNK_STATUS_VALUES:
            override_chunk_status = None

        try:
            fmt, raw = _decode_import_payload(payload)
        except Exception as exc:
            return _record_failed_job(conn, job_id, title, source_url, "", now,
                                      ParseError(f"内容解码失败：{exc}"))

        try:
            content = extract_content(fmt, raw)
        except QuarantineError as exc:
            return _record_failed_job(conn, job_id, title, source_url, fmt, now, exc, quarantined=True)
        except ImportError_ as exc:
            return _record_failed_job(conn, job_id, title, source_url, fmt, now, exc)

        digest = sha256_hex(content)
        # Pre-check: cheap, catches the common repeat-import case.
        existing = conn.execute(
            "SELECT id FROM documents WHERE sha256=?", (digest,)
        ).fetchone()
        if existing:
            return _record_duplicate_job(conn, job_id, title, source_url, fmt, digest, now, existing[0])

        doc_id = str(uuid.uuid4())
        chunk_status = override_chunk_status or default_chunk_status(doc_status)
        chunks = chunk_content(content, chunk_status)
        try:
            conn.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, title, source_url, organization, document_number, publish_date,
                 doc_status, fmt, digest, len(content), content, now),
            )
        except sqlite3.IntegrityError:
            # Race: another connection inserted the same sha256 between the
            # pre-check and this INSERT. Roll back the partial doc row and
            # record a duplicate instead of surfacing a 500.
            conn.rollback()
            row = conn.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
            existing_id = row[0] if row else None
            return _record_duplicate_job(conn, job_id, title, source_url, fmt, digest, now, existing_id)
        for ch in chunks:
            conn.execute(
                "INSERT INTO evidence_chunks VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), doc_id, ch["chunk_index"], ch["char_start"],
                 ch["char_end"], ch["status"], ch["content"]),
            )
        conn.execute(
            "INSERT INTO import_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, title, source_url, fmt, "succeeded", doc_id, digest,
             None, None, 0, now),
        )
        conn.commit()
        return {"status": "succeeded", "job_id": job_id, "document_id": doc_id,
                "sha256": digest, "format": fmt, "doc_status": doc_status,
                "chunk_count": len(chunks), "char_count": len(content)}
    finally:
        conn.close()


def _record_duplicate_job(conn: sqlite3.Connection, job_id: str, title: str, source_url: str,
                          fmt: str, digest: str, now: str, existing_id: str | None) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO import_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, title, source_url, fmt, "duplicate", existing_id, digest,
         "duplicate", "内容 SHA256 与已有文档一致，未重复入库。", 0, now),
    )
    conn.commit()
    return {"status": "duplicate", "job_id": job_id, "document_id": existing_id,
            "sha256": digest, "message": "重复内容，已跳过入库。"}


def _record_failed_job(conn: sqlite3.Connection, job_id: str, title: str, source_url: str,
                       fmt: str, now: str, exc: ImportError_, quarantined: bool = False) -> dict[str, Any]:
    status = "quarantined" if quarantined else "failed"
    reason = str(exc)
    conn.execute(
        "INSERT INTO import_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, title, source_url, fmt, status, None, None,
         exc.reason_code, reason, 1 if quarantined else 0, now),
    )
    conn.commit()
    return {"status": status, "job_id": job_id, "error_code": exc.reason_code,
            "error_reason": reason, "quarantined": quarantined}


def list_documents() -> list[dict[str, Any]]:
    conn = db()
    cols = ["id", "title", "source_url", "organization", "document_number",
            "publish_date", "status", "format", "sha256", "char_count", "imported_at"]
    rows = conn.execute(
        f"SELECT {','.join(cols)} FROM documents ORDER BY imported_at DESC"
    ).fetchall()
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_document(doc_id: str) -> dict[str, Any] | None:
    conn = db()
    cols = ["id", "title", "source_url", "organization", "document_number",
            "publish_date", "status", "format", "sha256", "char_count", "content", "imported_at"]
    row = conn.execute(
        f"SELECT {','.join(cols)} FROM documents WHERE id=?", (doc_id,)
    ).fetchone()
    conn.close()
    return dict(zip(cols, row)) if row else None


def list_chunks(doc_id: str) -> list[dict[str, Any]]:
    conn = db()
    cols = ["id", "document_id", "chunk_index", "char_start", "char_end", "status", "content"]
    rows = conn.execute(
        f"SELECT {','.join(cols)} FROM evidence_chunks WHERE document_id=? ORDER BY chunk_index",
        (doc_id,),
    ).fetchall()
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def list_jobs(status: str = "") -> list[dict[str, Any]]:
    conn = db()
    cols = ["id", "title", "source_url", "format", "status", "document_id",
            "sha256", "error_code", "error_reason", "quarantined", "created_at"]
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
            self.json_response({"items": list_documents()})
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
            self.json_response({"items": list_chunks(self._query_param("document_id"))})
            return
        if self.path.startswith("/api/library/jobs"):
            self.json_response({"items": list_jobs(self._query_param("status"))})
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
                ok = result.get("status") in ("succeeded", "duplicate")
                self.json_response(result, HTTPStatus.CREATED if ok else HTTPStatus.UNPROCESSABLE_ENTITY)
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
