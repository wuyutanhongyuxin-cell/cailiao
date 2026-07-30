#!/usr/bin/env python3
"""Prepare / validate a Stage 2B real-query candidate set from public DoIT data.

Deterministic, stdlib-only intake for ROADMAP Stage 2B line 97 (a real anonymized
query set). It reads LOCAL DoIT-style records (Hugging Face dataset
`ChiyuSONG/dynamics-of-instruction-tuning`, license MIT), selects 50-100 Chinese
user prompts from the `Creative Writing` category, de-duplicates and rejects
too-short / empty / non-Chinese prompts, and writes a deterministic JSON artifact
that records the source URL, license, extraction method, record count, and per-prompt
SHA256 hashes — and NO assistant answers.

It never downloads anything (no network), never reads `.env` or secrets, and never
fabricates records: it only processes DoIT records a human has already downloaded
locally. If no local data is present, run only the tests (which use an invented
fixture clearly marked as fixture, not real evidence).

Subcommands:
  prepare  --input <file|dir> --output <artifact.json> [--min 50] [--max 100]
  validate --artifact <artifact.json>

Exit codes:
  0  success (prepare wrote an artifact / validate passed)
  1  prepare could not select enough valid prompts, or validate failed a check
  2  input could not be read / parsed

DoIT record shape (per the dataset card): each record has `messages` (user content
in `messages[0].content`), plus `idx`, `type`, and `question_format`. Creative
Writing records have `type == "Creative Writing"`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DOIT_SOURCE_URL = "https://huggingface.co/datasets/ChiyuSONG/dynamics-of-instruction-tuning"
DOIT_LICENSE = "MIT"
DOIT_CREATIVE_WRITING_TYPE = "Creative Writing"
EXTRACTION_METHOD = "doit_creative_writing_user_prompts_v1"
ARTIFACT_METHOD = "stage2b_real_query_candidate_set_v1"

MIN_CASES_DEFAULT = 50
MAX_CASES_DEFAULT = 100
MIN_PROMPT_CHARS = 6          # reject too-short prompts
MIN_CJK_CHARS = 2            # require at least some Chinese content
_CJK_RE = re.compile(r"[一-鿿]")


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _iter_records(path: Path):
    """Yield dict records from a JSON array file, a JSONL file, or a directory of them."""
    files: list[Path] = []
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.suffix.lower() in (".json", ".jsonl"))
    else:
        files = [path]
    for f in files:
        text = f.read_text(encoding="utf-8")
        stripped = text.lstrip()
        if stripped.startswith("["):
            data = json.loads(text)
            if isinstance(data, list):
                for rec in data:
                    if isinstance(rec, dict):
                        yield rec
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict):
                    yield rec


def _user_prompt(record: dict) -> str:
    """Extract the user prompt from a DoIT record (messages[0].content)."""
    messages = record.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return _norm(messages[0].get("content", ""))
    return ""


def _is_valid_prompt(text: str) -> bool:
    if len(text) < MIN_PROMPT_CHARS:
        return False
    if len(_CJK_RE.findall(text)) < MIN_CJK_CHARS:
        return False
    return True


def select_creative_writing_prompts(records, min_cases=MIN_CASES_DEFAULT,
                                    max_cases=MAX_CASES_DEFAULT) -> dict:
    """Select up to ``max_cases`` unique, valid Chinese Creative Writing user prompts.

    Deterministic: preserves first-seen order, de-duplicates on normalized text, and
    rejects too-short / empty / insufficiently-Chinese prompts. Returns
    {cases, scanned, creative_writing, rejected, duplicates}. NO assistant answers
    are retained — only the user prompt text and its hash.
    """
    seen: set[str] = set()
    cases: list[dict] = []
    scanned = 0
    creative = 0
    rejected = 0
    duplicates = 0
    for rec in records:
        scanned += 1
        if str(rec.get("type", "")).strip() != DOIT_CREATIVE_WRITING_TYPE:
            continue
        creative += 1
        prompt = _user_prompt(rec)
        if not _is_valid_prompt(prompt):
            rejected += 1
            continue
        if prompt in seen:
            duplicates += 1
            continue
        seen.add(prompt)
        if len(cases) < max_cases:
            cases.append({
                "id": f"doit-cw-{len(cases) + 1:03d}",
                "query": prompt,
                "query_sha256": _sha256(prompt),
                "source_type": DOIT_CREATIVE_WRITING_TYPE,
                "question_format": _norm(rec.get("question_format", "")),
                "source_idx": rec.get("idx"),
            })
    return {"cases": cases, "scanned": scanned, "creative_writing": creative,
            "rejected": rejected, "duplicates": duplicates}


def build_artifact(records, min_cases=MIN_CASES_DEFAULT, max_cases=MAX_CASES_DEFAULT) -> dict:
    """Build the deterministic real-query candidate artifact from DoIT records."""
    sel = select_creative_writing_prompts(records, min_cases, max_cases)
    cases = sel["cases"]
    corpus_hash = _sha256("|".join(c["query_sha256"] for c in cases))
    return {
        "method": ARTIFACT_METHOD,
        "boundary": (
            "public real-query CANDIDATE evidence from a licensed open dataset; user prompts "
            "only (no assistant answers), extracted deterministically from locally-downloaded "
            "DoIT records; not private production telemetry and not a synthetic placeholder"
        ),
        "source": {
            "name": "ChiyuSONG/dynamics-of-instruction-tuning (DoIT)",
            "url": DOIT_SOURCE_URL,
            "license": DOIT_LICENSE,
            "category": DOIT_CREATIVE_WRITING_TYPE,
        },
        "extraction_method": EXTRACTION_METHOD,
        "record_count": len(cases),
        "min_cases": min_cases,
        "max_cases": max_cases,
        "selection_stats": {k: sel[k] for k in ("scanned", "creative_writing", "rejected", "duplicates")},
        "set_hash": corpus_hash,
        "cases": cases,
        "contains_assistant_answers": False,
        "roadmap_parent_items_checked": False,
    }


def validate_artifact(artifact) -> dict:
    """Validate a real-query candidate artifact's schema, count, license, and content.

    Checks: object shape; method/source/license/extraction_method present and correct;
    record_count in [min_cases, max_cases]; each case has id/query/query_sha256 with a
    correct hash and a valid Chinese prompt; unique ids and unique query hashes; and no
    assistant answers anywhere. Returns {passed, errors, warnings, record_count}.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(artifact, dict):
        return {"passed": False, "errors": [f"artifact must be a JSON object, got {type(artifact).__name__}"],
                "warnings": [], "record_count": 0}

    if artifact.get("method") != ARTIFACT_METHOD:
        errors.append(f"method must be '{ARTIFACT_METHOD}'")
    source = artifact.get("source", {})
    if not isinstance(source, dict):
        errors.append("'source' must be an object")
        source = {}
    if source.get("url") != DOIT_SOURCE_URL:
        errors.append(f"source.url must be the DoIT dataset URL")
    if source.get("license") != DOIT_LICENSE:
        errors.append(f"source.license must be '{DOIT_LICENSE}'")
    if not str(artifact.get("extraction_method", "")).strip():
        errors.append("'extraction_method' must be a non-empty string")
    if artifact.get("contains_assistant_answers") is not False:
        errors.append("'contains_assistant_answers' must be false")

    min_cases = artifact.get("min_cases", MIN_CASES_DEFAULT)
    max_cases = artifact.get("max_cases", MAX_CASES_DEFAULT)
    cases = artifact.get("cases")
    if not isinstance(cases, list):
        errors.append("'cases' must be a list")
        cases = []
    count = len(cases)
    if artifact.get("record_count") != count:
        errors.append(f"record_count {artifact.get('record_count')} != actual case count {count}")
    if not (isinstance(min_cases, int) and isinstance(max_cases, int) and min_cases <= count <= max_cases):
        errors.append(f"case count {count} must be within [{min_cases}, {max_cases}]")

    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        where = f"case #{idx}"
        if not isinstance(case, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = case.get("id")
        if not (isinstance(cid, str) and cid.strip()):
            errors.append(f"{where}: 'id' must be a non-empty string")
        elif cid in seen_ids:
            errors.append(f"{where}: duplicate id '{cid}'")
        else:
            seen_ids.add(cid)
        query = case.get("query")
        if not (isinstance(query, str) and _is_valid_prompt(_norm(query))):
            errors.append(f"{where}: 'query' must be a valid Chinese prompt")
        h = case.get("query_sha256")
        if not (isinstance(h, str) and h.startswith("sha256:")):
            errors.append(f"{where}: 'query_sha256' must be a sha256 string")
        elif isinstance(query, str) and h != _sha256(_norm(query)):
            errors.append(f"{where}: 'query_sha256' does not match query")
        elif h in seen_hashes:
            errors.append(f"{where}: duplicate query hash")
        else:
            seen_hashes.add(h)
        # No assistant answers may be embedded.
        for banned in ("answer", "assistant", "response", "output", "completion"):
            if banned in case:
                errors.append(f"{where}: must not carry an assistant-answer field '{banned}'")

    # set_hash must exist, be a sha256 string, and (when per-case hashes are all
    # present) equal the sha256 over the ordered per-case query_sha256 values —
    # the same computation build_artifact uses. This closes a tamper gap where
    # cases could be reordered/edited without the set_hash detecting it.
    set_hash = artifact.get("set_hash")
    if not (isinstance(set_hash, str) and set_hash.startswith("sha256:")):
        errors.append("'set_hash' must be a sha256 string")
    else:
        case_hashes = [c.get("query_sha256") for c in cases if isinstance(c, dict)]
        if case_hashes and all(isinstance(h, str) and h.startswith("sha256:") for h in case_hashes):
            expected = _sha256("|".join(case_hashes))
            if set_hash != expected:
                errors.append("'set_hash' does not match the sha256 over ordered per-case query_sha256 values")

    return {"passed": not errors, "errors": errors, "warnings": warnings, "record_count": count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prepare_stage2b_real_query_set",
        description="Prepare/validate a Stage 2B real-query candidate set from local DoIT data (no network).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare", help="build an artifact from local DoIT records")
    p_prep.add_argument("--input", required=True, help="DoIT JSON/JSONL file or directory")
    p_prep.add_argument("--output", required=True, help="artifact JSON output path")
    p_prep.add_argument("--min", type=int, default=MIN_CASES_DEFAULT)
    p_prep.add_argument("--max", type=int, default=MAX_CASES_DEFAULT)

    p_val = sub.add_parser("validate", help="validate an existing artifact")
    p_val.add_argument("--artifact", required=True, help="artifact JSON to validate")

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.cmd == "prepare":
        try:
            records = list(_iter_records(Path(args.input)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": f"failed to read input: {exc}"}, ensure_ascii=False))
            return 2
        artifact = build_artifact(records, args.min, args.max)
        report = validate_artifact(artifact)
        if not report["passed"] or artifact["record_count"] < args.min:
            print(json.dumps({"ok": False, "record_count": artifact["record_count"],
                              "errors": report["errors"] or [f"only {artifact['record_count']} valid prompts (< {args.min})"],
                              "selection_stats": artifact["selection_stats"]}, ensure_ascii=False, indent=2))
            return 1
        Path(args.output).write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": args.output, "record_count": artifact["record_count"],
                          "set_hash": artifact["set_hash"]}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "validate":
        try:
            with Path(args.artifact).open("r", encoding="utf-8") as f:
                artifact = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"passed": False, "errors": [f"failed to load artifact: {exc}"]}, ensure_ascii=False))
            return 2
        report = validate_artifact(artifact)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
