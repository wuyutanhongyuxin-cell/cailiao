#!/usr/bin/env python3
"""Build + validate (and optionally summarize) a Stage 5 comparison-baseline matrix.

Usage:
    python tools/validate_comparison_baselines.py \
        --suite tests/data/benchmark_suite_sample.json \
        --outputs tests/data/comparison_baselines_sample.json --json
    python tools/validate_comparison_baselines.py --suite <suite> --outputs <outputs> \
        --scores tests/data/comparison_baseline_scores_sample.json --json

with --scores aggregates supplied numeric case scores into per-arm means
(human / generic-prompt / project-prompt / model arms), validates its shape, and
鈥?with --scores 鈥?aggregates supplied numeric case scores into per-arm means
(never inventing scores). Never calls a model and never touches the network.
Exit code:
  0  validation passed (no errors)
  1  validation failed (errors present)
  2  an input file could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.build/validate/summarize_comparison_baseline_* for
a single source of truth on the rules.
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
        prog="validate_comparison_baselines",
        description="Build and validate a comparison-baseline matrix (no model/network access).")
    parser.add_argument("--suite", required=True, help="path to the benchmark suite JSON")
    parser.add_argument("--outputs", required=True, help="path to the comparison arm outputs JSON")
    parser.add_argument("--scores", help="optional path to reviewer scores JSON to summarize")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        suite = server.load_benchmark_suite(args.suite)
        outputs = _load_json(args.outputs)
        scores = _load_json(args.scores) if args.scores else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"passed": False, "errors": [f"failed to load input: {exc}"], "warnings": []}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    matrix = server.build_comparison_baseline_matrix(suite, outputs)
    report = server.validate_comparison_baseline_matrix(matrix)
    report = {**report, "matrix_metadata": matrix["metadata"]}
    if scores is not None:
        report["summary"] = server.summarize_comparison_baseline_scores(matrix, scores)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"validation: {'PASS' if report['passed'] else 'FAIL'}  "
              f"cases={report['case_count']}  "
              f"arms={','.join(report['arm_ids']) or '-'}  "
              f"types={','.join(report['arm_types']) or '-'}")
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        for w in report["warnings"]:
            print(f"  WARN:  {w}")
        if "summary" in report:
            for row in report["summary"]["per_arm"]:
                print(f"  summary: {row['arm_id']} ({row['arm_type']})  mean={row['mean_score']}")
            print(f"  best_arm_id: {report['summary']['best_arm_id']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
