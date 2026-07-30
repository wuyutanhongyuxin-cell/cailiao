#!/usr/bin/env python3
"""Validate a Stage 2B evaluation-run manifest against the eval-run contract.

Usage:
    python tools/check_stage2b_eval_run_contract.py --json
    python tools/check_stage2b_eval_run_contract.py --config path/to/eval_run.json --json

Validates the SHAPE of a declared eval-run manifest (identity/dataset readiness,
query-count band, required metrics as numbers, latency_p95 >= latency_p50,
qrels/runfile/result pointers + hashes, explicit acceptance verdict). It never
reads the files at the pointers, never verifies a hash against contents, and never
calls a provider. The default repo state has no real eval run. Exit code:
  0  a real eval-run manifest validates (declared shape only)
  1  no manifest / manifest does not validate (default repo state)
  2  the --config file could not be loaded

The manifest may be passed either as the top-level object or under an "eval_run"
key. Stdlib only; delegates to server.build_stage2b_eval_run_contract /
validate_stage2b_eval_run_manifest.
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
        prog="check_stage2b_eval_run_contract",
        description="Validate a Stage 2B eval-run manifest shape (no file read, no network).")
    parser.add_argument("--config", default=None, help="optional path to an eval-run manifest JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"has_real_eval_run": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2
        # Accept either a bare manifest or one wrapped under "eval_run".
        config = loaded if isinstance(loaded, dict) and "eval_run" in loaded else {"eval_run": loaded}

    report = server.build_stage2b_eval_run_contract(config)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"has_real_eval_run: {report['has_real_eval_run']}  "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for e in report["validation"]["errors"]:
            print(f"  ERROR: {e}")
        for w in report["validation"]["warnings"]:
            print(f"  WARN:  {w}")
    return 0 if report["has_real_eval_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
