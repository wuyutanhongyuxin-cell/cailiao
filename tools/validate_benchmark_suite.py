#!/usr/bin/env python3
"""Validate (and optionally score) a Stage 5 benchmark suite's shape.

Usage:
    python tools/validate_benchmark_suite.py --suite tests/data/benchmark_suite_sample.json --json
    python tools/validate_benchmark_suite.py --suite <path> --json --score

Checks suite/case structure only (metadata name/version/anonymized, case id/genre,
prompt_fields/facts/evidence types, and expected_elements dimensions). With --score,
also runs the deterministic lexical scoring skeleton over each case's reference_answer.
Never calls a model and never touches the network. Exit code:
  0  validation passed (no errors; warnings are allowed)
  1  validation failed (errors present)
  2  suite could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.validate_benchmark_suite / score_benchmark_suite
for a single source of truth on the rules.
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
        prog="validate_benchmark_suite",
        description="Validate a benchmark suite's shape (no model/network access).")
    parser.add_argument("--suite", required=True, help="path to the benchmark suite JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument("--score", action="store_true",
                        help="also run the deterministic scoring skeleton over reference answers")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        suite = server.load_benchmark_suite(args.suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"passed": False, "errors": [f"failed to load suite: {exc}"],
                   "warnings": [], "suite_path": args.suite}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    report = server.validate_benchmark_suite(suite)
    if args.score:
        report = {**report, "scoring": server.score_benchmark_suite(suite)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"validation: {'PASS' if report['passed'] else 'FAIL'}  "
              f"cases={report['case_count']}  "
              f"genres={','.join(report['genres']) or '-'}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        for w in report["warnings"]:
            print(f"  WARN:  {w}")
        if args.score:
            agg = report["scoring"]["aggregate"]
            print(f"  score: overall={agg['overall']}  dimensions={agg['dimensions']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
