#!/usr/bin/env python3
"""Validate a retrieval eval suite's shape before it becomes a quality gate.

Usage:
    python tools/validate_retrieval_suite.py --suite tests/data/retrieval_eval_suite.json --json

Checks suite/case structure only (ids, query, supported filter keys, relevance
targets, min_authority/format sanity). Never loads the corpus into a DB and never
prints document content or secrets. Exit code:
  0  validation passed (no errors; warnings are allowed)
  1  validation failed (errors present)
  2  suite could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.validate_retrieval_suite for a single source of
truth on the rules.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_retrieval_suite",
        description="Validate a retrieval eval suite's shape (no corpus/DB access).")
    parser.add_argument("--suite", required=True, help="path to the eval suite JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        suite = server.load_retrieval_eval_suite(args.suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"passed": False, "errors": [f"failed to load suite: {exc}"],
                   "warnings": [], "suite_path": args.suite}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    report = server.validate_retrieval_suite(suite)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"validation: {'PASS' if report['passed'] else 'FAIL'}  "
              f"cases={report['case_count']}  "
              f"filters={','.join(report['filter_keys_used']) or '-'}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        for w in report["warnings"]:
            print(f"  WARN:  {w}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
