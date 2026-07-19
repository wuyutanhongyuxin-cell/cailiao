from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime
from html import escape
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, title TEXT, source TEXT, url TEXT, body TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(id UNINDEXED, title, source, body)"
    )
    conn.commit()
    return conn


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
        return super().do_GET()

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
