#!/usr/bin/env python3
"""Validate a real anonymized query set and report its intake readiness.

Usage:
    python tools/validate_real_query_set.py --set tests/data/real_query_set_template.json --json

Validates shape, anonymization (no PII/secret fields or PII-shaped values), and
provenance, then classifies readiness (invalid / template / incomplete_real /
ready_real / oversized_real). Never fabricates data and never prints a raw PII
value (only the matched kind). No model call, no network. Exit code:
  0  the set is a completed, ready real set (status == ready_real)
  1  not ready (template / incomplete / oversized / structurally invalid)
  2  the set could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.load_real_query_set / summarize_real_query_readiness
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
        prog="validate_real_query_set",
        description="Validate a real anonymized query set and report readiness (no PII output, no network).")
    parser.add_argument("--set", dest="path", required=True, help="path to the query-set JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        dataset = server.load_real_query_set(args.path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"ready": False, "status": "load_error", "errors": [f"failed to load set: {exc}"]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    report = server.summarize_real_query_readiness(dataset)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"readiness: {report['status']}  ready={report['ready']}  "
              f"cases={report['case_count']} (need {report['min_cases']}-{report['max_cases']})")
        for r in report["reasons"]:
            print(f"  - {r}")
        for e in report["validation"]["errors"]:
            print(f"  ERROR: {e}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
