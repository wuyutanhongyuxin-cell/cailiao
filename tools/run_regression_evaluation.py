#!/usr/bin/env python3
"""Assemble, validate, and summarize a Stage 5 regression-evaluation run.

Usage:
    python tools/run_regression_evaluation.py --config tests/data/regression_run_sample.json --json

Builds a regression-run record from a config (trigger, baseline/candidate refs,
suite, component reports), validates its shape, and summarizes it into an overall
already-produced component reports only; it never executes an evaluation, calls a
already-produced component reports only 鈥?it never executes an evaluation, calls a
model, or touches the network. Exit code:
  0  validation passed (no errors; warnings are allowed)
  1  validation failed (errors present)
  2  config could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.build/validate/summarize_regression_evaluation_run
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
        prog="run_regression_evaluation",
        description="Assemble/validate/summarize a regression-evaluation run (no model/network).")
    parser.add_argument("--config", required=True, help="path to the regression run config JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        with Path(args.config).open("r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"passed": False, "errors": [f"failed to load config: {exc}"], "warnings": []}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    run = server.build_regression_evaluation_run(config)
    report = server.validate_regression_evaluation_run(run)
    report = {**report, "run_metadata": run["metadata"],
              "summary": server.summarize_regression_evaluation_run(run)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(f"validation: {'PASS' if report['passed'] else 'FAIL'}  "
              f"status={summary['status']}  reports={report['report_count']}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        for w in report["warnings"]:
            print(f"  WARN:  {w}")
        print(f"  totals: {summary['totals']}")
        for row in summary["reports"]:
            print(f"  {row['name']} ({row['status']})  deltas={row['metric_deltas']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
