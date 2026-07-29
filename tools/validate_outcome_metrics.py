#!/usr/bin/env python3
"""Validate (and optionally summarize) a Stage 5 outcome-metrics log.

Usage:
    python tools/validate_outcome_metrics.py --log tests/data/outcome_metrics_sample.json --json
    python tools/validate_outcome_metrics.py --log <path> --json --summary

Checks outcome-metrics log shape (per case/arm identity, numeric ranges, ISO-like
timestamps when duration is absent). With --summary, also aggregates deterministic
metrics (adoption_rate, edit_distance, duration_seconds, rework_rounds) per arm and
overall. Never calls a model and never touches the network. Exit code:
  0  validation passed (no errors; warnings are allowed)
  1  validation failed (errors present)
  2  log could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.validate_outcome_metrics_log / summarize_outcome_metrics
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
        prog="validate_outcome_metrics",
        description="Validate an outcome-metrics log's shape (no model/network access).")
    parser.add_argument("--log", required=True, help="path to the outcome-metrics log JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument("--summary", action="store_true",
                        help="also aggregate deterministic metrics per arm and overall")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        log = server.load_outcome_metrics_log(args.log)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"passed": False, "errors": [f"failed to load log: {exc}"],
                   "warnings": [], "log_path": args.log}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    report = server.validate_outcome_metrics_log(log)
    if args.summary:
        report = {**report, "summary": server.summarize_outcome_metrics(log)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"validation: {'PASS' if report['passed'] else 'FAIL'}  "
              f"rows={report['row_count']}  "
              f"arms={','.join(report['arm_ids']) or '-'}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        for w in report["warnings"]:
            print(f"  WARN:  {w}")
        if args.summary:
            for row in report["summary"]["per_arm"]:
                print(f"  arm {row['arm_id']}: {row['metrics']}")
            print(f"  overall: {report['summary']['overall']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
