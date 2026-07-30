#!/usr/bin/env python3
"""Check real NLI / semantic-conflict PRODUCTION readiness.

Usage:
    python tools/check_semantic_conflict_readiness.py --json
    python tools/check_semantic_conflict_readiness.py --config path/to/nli_config.json --json

Reports whether a DECLARED config is production-ready for semantic conflict/
entailment detection: a real NLI/LLM provider (with a credential SOURCE, never a
value), an eval-label set covering supports/refutes/not_enough_info, and a valid
conflict policy. With no --config it evaluates the repo's current shipped state
(deterministic LEXICAL detector), which is NEVER semantic-ready. Makes no model
call, installs nothing, reads no credentials. Exit code:
  0  production_ready == true
  1  not production-ready (missing items listed)
  2  the --config file could not be loaded

Stdlib only; delegates to server.build_semantic_conflict_readiness.
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
        prog="check_semantic_conflict_readiness",
        description="Check real NLI / semantic-conflict production readiness (no network).")
    parser.add_argument("--config", default=None, help="optional path to a declared NLI config JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"production_ready": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2

    report = server.build_semantic_conflict_readiness(config)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"production_ready: {report['production_ready']}")
        for m in report["missing"]:
            print(f"  MISSING: {m}")
        if not args.config:
            print("  note: no --config given; evaluated the repo's current lexical detector state.")
    return 0 if report["production_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
