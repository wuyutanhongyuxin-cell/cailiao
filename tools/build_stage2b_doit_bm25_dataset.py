#!/usr/bin/env python3
"""Build / validate a ready_real DoIT BM25 calibration dataset (query set + corpus).

Deterministic, stdlib-only. Turns LOCAL DoIT prompt-answer records (Hugging Face
dataset `ChiyuSONG/dynamics-of-instruction-tuning`, license MIT) into a query set with
a PAIRED corpus, suitable for the existing gated BM25 sweep
(`server.run_bm25_sweep_on_real_query_set`, which requires `summarize_real_query_readiness`
== `ready_real` AND a `corpus`).

For each selected Creative Writing record:
  - the user prompt (`messages[0].content`) becomes a case `query`;
  - the assistant answer (`messages[1].content`) becomes a paired PUBLIC corpus document
    (this is public MIT benchmark data, NOT private production telemetry);
  - the case's `relevant_titles` points at that paired corpus doc's title.

It reads only local files (never downloads), never reads `.env`/secrets, and never
fabricates rows: if fewer than `--min` valid records are found it exits nonzero and writes
nothing. Cases carry NO assistant answers (the answer lives only in `corpus[]`).

Category matching reuses the intake tool's aliases, so both the dataset-card display label
`Creative Writing` and the actual file label `creative_writing` are accepted.

Subcommands:
  build    --input <DoIT JSON/JSONL file|dir> --output <dataset.json> [--min 50] [--max 100]
           [--source-file <name>] [--collected-at <iso>]
  validate --set <dataset.json>

Exit codes: 0 success; 1 not enough valid records / validation failed; 2 input unreadable.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

# PII-shaped value patterns (mirror server._REAL_QUERY_PII_PATTERNS) used as a
# belt-and-suspenders pre-filter; the server's validate_real_query_set also rejects
# these at validation time.
_PII_PATTERNS = (
    re.compile(r"\b\d{17}[\dXx]\b"),                                  # id card
    re.compile(r"\b1[3-9]\d{9}\b"),                                  # cn phone
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),  # email
)

_TOOLS_DIR = Path(__file__).resolve().parent
_INTAKE = _TOOLS_DIR / "prepare_stage2b_real_query_set.py"


def _load_intake():
    spec = importlib.util.spec_from_file_location("prepare_stage2b_real_query_set", _INTAKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_intake = _load_intake()

DOIT_SOURCE_URL = _intake.DOIT_SOURCE_URL
DOIT_LICENSE = _intake.DOIT_LICENSE
DOIT_CREATIVE_WRITING_TYPE = _intake.DOIT_CREATIVE_WRITING_TYPE
DATASET_METHOD = "stage2b_doit_bm25_dataset_v1"
EXTRACTION_METHOD = "doit_creative_writing_prompt_answer_bm25_v1"
DEFAULT_COLLECTED_AT = "2026-01-01T00:00:00Z"  # extraction timestamp, not user data
MIN_CASES_DEFAULT = 50
MAX_CASES_DEFAULT = 100
MIN_ANSWER_CHARS = 8


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assistant_answer(record: dict) -> str:
    messages = record.get("messages")
    if isinstance(messages, list) and len(messages) >= 2 and isinstance(messages[1], dict):
        return _intake._norm(messages[1].get("content", ""))
    return ""


def build_dataset(records, source_file="", collected_at=DEFAULT_COLLECTED_AT,
                  min_cases=MIN_CASES_DEFAULT, max_cases=MAX_CASES_DEFAULT) -> dict:
    """Build a ready_real query set + paired corpus from DoIT prompt-answer records.

    Selects unique valid Chinese Creative Writing prompts (alias-tolerant) that ALSO have
    a non-empty assistant answer. Deterministic (first-seen order, de-duplicated on prompt).
    Cases carry only the query + hashes + a relevant_titles target; the assistant answer is
    stored solely as a paired public corpus document.
    """
    seen: set[str] = set()
    cases: list[dict] = []
    corpus: list[dict] = []
    scanned = creative = rejected = duplicates = no_answer = 0
    for rec in records:
        scanned += 1
        if not _intake._is_creative_writing_type(rec.get("type", "")):
            continue
        creative += 1
        prompt = _intake._user_prompt(rec)
        if not _intake._is_valid_prompt(prompt):
            rejected += 1
            continue
        # Anonymization: reject any prompt carrying a PII-shaped value.
        if _intake_pii(prompt):
            rejected += 1
            continue
        answer = _assistant_answer(rec)
        if len(answer) < MIN_ANSWER_CHARS:
            no_answer += 1
            continue
        if prompt in seen:
            duplicates += 1
            continue
        seen.add(prompt)
        if len(cases) >= max_cases:
            continue
        n = len(cases) + 1
        doc_title = f"doit-cw-doc-{n:03d}"
        doc_id = f"doit-cw-corpus-{n:03d}"
        cases.append({
            "id": f"doit-cw-{n:03d}",
            "query": prompt,
            "query_sha256": _sha256(prompt),
            "provenance": {"source": source_file or DOIT_SOURCE_URL,
                           "collected_at": collected_at, "anonymized": True},
            "relevant_titles": [doc_title],
            "answer_sha256": _sha256(answer),
            "source_type": DOIT_CREATIVE_WRITING_TYPE,
            "source_type_raw": _intake._norm(rec.get("type", "")),
            "source_idx": rec.get("idx"),
        })
        corpus.append({
            "id": doc_id,
            "title": doc_title,
            "text": answer,
            "format": "txt",
            "status": "有效",
        })

    set_hash = _sha256("|".join(c["query_sha256"] for c in cases))
    corpus_hash = _sha256("|".join(d["title"] + ":" + _sha256(d["text"]) for d in corpus))
    return {
        "method": DATASET_METHOD,
        "metadata": {
            # NOTE: name/version/description/kind must avoid placeholder tokens so the
            # readiness classifier does not treat this as a template.
            "name": "DoIT Creative Writing BM25 calibration query set",
            "version": "v1",
            "description": "Public MIT DoIT Creative Writing user prompts paired with their "
                           "public assistant answers as retrieval documents, for BM25 calibration.",
            "source_url": DOIT_SOURCE_URL,
            "source_file": source_file,
            "license": DOIT_LICENSE,
            "category": DOIT_CREATIVE_WRITING_TYPE,
            "extraction_method": EXTRACTION_METHOD,
            "anonymized": True,
            "is_template": False,
            "boundary": (
                "public real benchmark seed (MIT DoIT), not private production telemetry and "
                "not final production calibration by itself"
            ),
        },
        "record_count": len(cases),
        "min_cases": min_cases,
        "max_cases": max_cases,
        "selection_stats": {"scanned": scanned, "creative_writing": creative,
                            "rejected": rejected, "duplicates": duplicates, "no_answer": no_answer},
        "set_hash": set_hash,
        "corpus_hash": corpus_hash,
        "cases": cases,
        "corpus": corpus,
        "contains_assistant_answers_in_cases": False,
        "roadmap_parent_items_checked": False,
    }


def _intake_pii(text: str) -> bool:
    for pat in _PII_PATTERNS:
        if pat.search(text or ""):
            return True
    return False


def validate_dataset(dataset, server=None) -> dict:
    """Validate the dataset: server readiness == ready_real, corpus paired, hashes, no answers.

    Delegates the query-set shape/anonymization/readiness check to the server helpers
    (`summarize_real_query_readiness`) when available, then adds dataset-specific checks:
    license/source metadata, a paired corpus (every case's relevant_titles resolves to a
    corpus doc title), set_hash integrity, and that no case embeds an assistant answer.
    Returns {passed, errors, warnings, status, record_count, ready_for_bm25}.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(dataset, dict):
        return {"passed": False, "errors": [f"dataset must be a JSON object, got {type(dataset).__name__}"],
                "warnings": [], "status": None, "record_count": 0, "ready_for_bm25": False}

    meta = dataset.get("metadata", {}) if isinstance(dataset.get("metadata"), dict) else {}
    if meta.get("license") != DOIT_LICENSE:
        errors.append(f"metadata.license must be '{DOIT_LICENSE}'")
    if meta.get("source_url") != DOIT_SOURCE_URL:
        errors.append("metadata.source_url must be the DoIT dataset URL")

    cases = dataset.get("cases") if isinstance(dataset.get("cases"), list) else []
    corpus = dataset.get("corpus") if isinstance(dataset.get("corpus"), list) else []
    if not corpus:
        errors.append("dataset must contain a non-empty 'corpus'")
    corpus_titles = {d.get("title") for d in corpus if isinstance(d, dict)}

    # Every case must have a relevance target that resolves to a corpus doc title.
    for idx, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            errors.append(f"case #{idx}: must be an object")
            continue
        titles = case.get("relevant_titles")
        if not (isinstance(titles, list) and titles):
            errors.append(f"case '{case.get('id', idx)}': must declare relevant_titles")
            continue
        if not all(t in corpus_titles for t in titles):
            errors.append(f"case '{case.get('id', idx)}': relevant_titles not all present in corpus")
        for banned in ("answer", "assistant", "response", "output", "completion"):
            if banned in case:
                errors.append(f"case '{case.get('id', idx)}': must not embed assistant-answer field '{banned}'")

    # set_hash integrity (matches build_dataset).
    set_hash = dataset.get("set_hash")
    case_hashes = [c.get("query_sha256") for c in cases if isinstance(c, dict)]
    if not (isinstance(set_hash, str) and set_hash.startswith("sha256:")):
        errors.append("'set_hash' must be a sha256 string")
    elif case_hashes and all(isinstance(h, str) and h.startswith("sha256:") for h in case_hashes):
        if set_hash != _sha256("|".join(case_hashes)):
            errors.append("'set_hash' does not match ordered per-case query_sha256 values")

    # Delegate readiness to the server helpers (single source of truth) when available.
    status = None
    if server is not None:
        readiness = server.summarize_real_query_readiness(dataset)
        status = readiness["status"]
        if status != "ready_real":
            errors.append(f"summarize_real_query_readiness status is '{status}', not 'ready_real'")

    ready_for_bm25 = (not errors) and (status == "ready_real") and bool(corpus)
    return {"passed": not errors, "errors": errors, "warnings": warnings, "status": status,
            "record_count": len(cases), "ready_for_bm25": ready_for_bm25}


def _load_server():
    server_path = _TOOLS_DIR.parent / "backend" / "server.py"
    spec = importlib.util.spec_from_file_location("server", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_stage2b_doit_bm25_dataset",
        description="Build/validate a ready_real DoIT BM25 calibration dataset (no network).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="build a query set + corpus from local DoIT records")
    p_build.add_argument("--input", required=True, help="DoIT JSON/JSONL file or directory")
    p_build.add_argument("--output", required=True, help="dataset JSON output path")
    p_build.add_argument("--min", type=int, default=MIN_CASES_DEFAULT)
    p_build.add_argument("--max", type=int, default=MAX_CASES_DEFAULT)
    p_build.add_argument("--source-file", default="", help="exact source file name/path (recorded in metadata)")
    p_build.add_argument("--collected-at", default=DEFAULT_COLLECTED_AT)

    p_val = sub.add_parser("validate", help="validate an existing dataset")
    p_val.add_argument("--set", dest="path", required=True, help="dataset JSON to validate")

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.cmd == "build":
        try:
            records = list(_intake._iter_records(Path(args.input)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": f"failed to read input: {exc}"}, ensure_ascii=False))
            return 2
        dataset = build_dataset(records, source_file=args.source_file, collected_at=args.collected_at,
                                min_cases=args.min, max_cases=args.max)
        server = _load_server()
        report = validate_dataset(dataset, server=server)
        if dataset["record_count"] < args.min or not report["passed"]:
            print(json.dumps({"ok": False, "record_count": dataset["record_count"],
                              "errors": report["errors"] or [f"only {dataset['record_count']} valid records (< {args.min})"],
                              "selection_stats": dataset["selection_stats"]}, ensure_ascii=False, indent=2))
            return 1
        Path(args.output).write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": args.output, "record_count": dataset["record_count"],
                          "status": report["status"], "ready_for_bm25": report["ready_for_bm25"],
                          "set_hash": dataset["set_hash"]}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "validate":
        try:
            with Path(args.path).open("r", encoding="utf-8") as f:
                dataset = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"passed": False, "errors": [f"failed to load dataset: {exc}"]}, ensure_ascii=False))
            return 2
        report = validate_dataset(dataset, server=_load_server())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
