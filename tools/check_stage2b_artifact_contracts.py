#!/usr/bin/env python3
"""Report Stage 2B artifact contracts (declared-shape templates).

Usage:
    python tools/check_stage2b_artifact_contracts.py --json
    python tools/check_stage2b_artifact_contracts.py --config path/to/artifacts.json --json
    python tools/check_stage2b_artifact_contracts.py --require-ready-artifacts --json

Emits the five Stage 2B artifact contracts (required/forbidden fields, validator,
proves / does_not_prove) plus the placeholder-only example path. Default exit is 0:
the contracts and template exist and are valid as documentation. It reports that NO
real artifacts are supplied and that ROADMAP parent items remain unchecked. With
--require-ready-artifacts, exit is 1 unless all five external-dependency blockers
are satisfied by supplied config metadata (declared shape only, never live
validation). Exit code:
  0  contracts available (default), or all artifacts ready under --require-ready-artifacts
  1  --require-ready-artifacts given and not all artifacts satisfied
  2  the --config file could not be loaded

Stdlib only; delegates to server.build_stage2b_artifact_contracts / build_external_dependency_audit.
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
        prog="check_stage2b_artifact_contracts",
        description="Report Stage 2B artifact contracts (no network, no credentials).")
    parser.add_argument("--config", default=None, help="optional path to a declared artifacts config JSON")
    parser.add_argument("--require-ready-artifacts", action="store_true",
                        help="exit nonzero unless all 5 external-dependency blockers are satisfied by config")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"contract_count": 0, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2

    report = server.build_stage2b_artifact_contracts(config)
    audit = server.build_external_dependency_audit(config)
    all_ready = audit["all_external_dependencies_satisfied"]
    report = {**report,
              "real_artifacts_supplied": report["ready_artifact_count"] > 0,
              "all_external_dependencies_satisfied": all_ready,
              "note": ("contracts/templates exist and are valid as docs; no real artifacts are "
                       "supplied by default and ROADMAP parent items remain unchecked")}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"contracts: {report['contract_count']}  ready_artifacts: {report['ready_artifact_count']}  "
              f"parents_checked: {report['roadmap_parent_items_checked']}")
        print(f"  {report['note']}")
        if args.require_ready_artifacts and not all_ready:
            print("  --require-ready-artifacts: not all external-dependency blockers satisfied")

    if args.require_ready_artifacts:
        return 0 if all_ready else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
