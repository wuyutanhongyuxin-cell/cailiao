#!/usr/bin/env python3
"""Calibrate BM25 (k1/b/threshold) on a REAL anonymized query set — gated.

Usage:
    python tools/sweep_bm25_real_queries.py --set path/to/real_query_set.json --json

Runs a deterministic BM25 sweep ONLY when the supplied query set is a completed,
ready real set (summarize_real_query_readiness(...).status == "ready_real") that
also carries a corpus. Template / incomplete / synthetic / corpus-less sets are
refused without running anything — this never fabricates a calibration result.
No network, no model call. Exit code:
  0  sweep ran and produced a best (k1, b, threshold)
  1  refused (set not ready_real, no corpus, or invalid grid)
  2  the set could not be loaded (missing/invalid JSON)

Stdlib only; delegates to server.load_real_query_set / run_bm25_sweep_on_real_query_set.
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
        prog="sweep_bm25_real_queries",
        description="Gated BM25 calibration on a ready real anonymized query set (no network/model).")
    parser.add_argument("--set", dest="path", required=True, help="path to the real query-set JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    try:
        dataset = server.load_real_query_set(args.path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ran": False, "refused": True, "reason": f"failed to load set: {exc}"},
                         ensure_ascii=False, indent=2))
        return 2

    report = server.run_bm25_sweep_on_real_query_set(dataset)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if report.get("ran"):
            best = report["best"]
            print(f"sweep ran: {report['candidate_count']} candidates; "
                  f"best k1={best['k1']} b={best['b']} threshold={best['threshold']} "
                  f"(title_recall={best['title_recall_at_k']}, chunk_recall={best['chunk_recall_at_k']})")
        else:
            print(f"REFUSED: {report.get('reason')}")
    return 0 if report.get("ran") else 1


if __name__ == "__main__":
    raise SystemExit(main())
