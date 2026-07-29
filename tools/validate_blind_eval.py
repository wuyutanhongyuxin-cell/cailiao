#!/usr/bin/env python3
"""Build + validate (and optionally reveal) a Stage 5 blind-evaluation pack.

Usage:
    python tools/validate_blind_eval.py \
        --suite tests/data/benchmark_suite_sample.json \
        --candidates tests/data/blind_eval_candidates_sample.json --json
    python tools/validate_blind_eval.py --suite <suite> --candidates <cands> \
        --reveal tests/data/blind_eval_scores_sample.json --json

Builds an evaluator-facing blind pack (identity hidden behind stable labels),
validates its shape and identity hygiene, and with --reveal joins reviewer
scores back to the hidden candidate identity for per-candidate aggregates.
Never calls a model and never touches the network. Exit code:
  0  validation passed (no errors)
  1  validation failed (errors present)
  2  an input file could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.build/validate/reveal_blind_evaluation_* for a
single source of truth on the rules.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: str):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_blind_eval",
        description="Build and validate a blind-evaluation pack (no model/network access).")
    parser.add_argument("--suite", required=True, help="path to the benchmark suite JSON")
    parser.add_argument("--candidates", required=True, help="path to the candidates JSON")
    parser.add_argument("--reveal", help="optional path to reviewer scores JSON to reveal")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        suite = server.load_benchmark_suite(args.suite)
        candidates = _load_json(args.candidates)
        scores = _load_json(args.reveal) if args.reveal else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"passed": False, "errors": [f"failed to load input: {exc}"], "warnings": []}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    pack = server.build_blind_evaluation_pack(suite, candidates)
    report = server.validate_blind_evaluation_pack(pack)
    report = {**report, "pack_metadata": pack["metadata"]}
    if scores is not None:
        report["reveal"] = server.reveal_blind_evaluation_results(pack, scores)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"validation: {'PASS' if report['passed'] else 'FAIL'}  "
              f"cases={report['case_count']}  "
              f"blind_ids={','.join(report['blind_ids']) or '-'}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        for w in report["warnings"]:
            print(f"  WARN:  {w}")
        if "reveal" in report:
            for row in report["reveal"]["per_candidate"]:
                print(f"  reveal: {row['blind_id']} -> {row['identity']}  mean={row['mean_score']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
