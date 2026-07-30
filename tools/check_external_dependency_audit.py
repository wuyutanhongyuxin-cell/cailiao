#!/usr/bin/env python3
"""Final external-dependency audit: gate the 5 real-world ROADMAP blockers.

Usage:
    python tools/check_external_dependency_audit.py --json
    python tools/check_external_dependency_audit.py --config path/to/artifacts.json --json

Aggregates the five ROADMAP items that cannot honestly be completed inside this
repo without human data or real provider credentials (real query set, real-query
BM25 calibration, real embedding provider + persistent store + index, real
reranker/cross-encoder + RRF, real NLI/LLM semantic-conflict). A blocker is only
"satisfied" when --config declares an artifact whose METADATA SHAPE passes the
matching readiness helper — this never reads credentials, resolves a
credential_source to a secret, or contacts a provider. Exit code:
  0  all_external_dependencies_satisfied == true (declared metadata only)
  1  outstanding blockers remain (default repo state)
  2  the --config file could not be loaded

No ROADMAP parent item is auto-checked. Stdlib only; delegates to
server.build_external_dependency_audit.
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
        prog="check_external_dependency_audit",
        description="Audit the 5 real-world external-dependency blockers (no network, no credentials).")
    parser.add_argument("--config", default=None, help="optional path to a declared artifacts config JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"all_external_dependencies_satisfied": False,
                              "error": f"failed to load config: {exc}"}, ensure_ascii=False, indent=2))
            return 2

    report = server.build_external_dependency_audit(config)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"all_external_dependencies_satisfied: {report['all_external_dependencies_satisfied']}  "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for b in report["blockers"]:
            mark = "OK " if b["satisfied"] else "BLOCKED"
            print(f"  [{mark}] {b['id']} (ROADMAP line {b['roadmap_line']}): {b['detail']}")
    return 0 if report["all_external_dependencies_satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
