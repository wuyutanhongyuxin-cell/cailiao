#!/usr/bin/env python3
"""Validate the aggregate Stage 2B evidence package readiness.

Usage:
    python tools/check_stage2b_evidence_package_validator.py --json
    python tools/check_stage2b_evidence_package_validator.py --config path/to/artifacts.json --json

Aggregates all Stage 2B external-blocker evidence into one machine-readable
readiness report by reusing the existing audit + seven contract/status helpers
(declared artifacts, eval-run manifest, observability snapshot, release dossier,
reproducibility/provenance, risk treatment, industry checklist). For each of the
five blockers it lists, per evidence group, the required item that would close it,
and reports each group's current readiness. Metadata/package validator only: no
network, no provider call, no model download, no eval, no artifact/hash file read,
no credential/.env read, no approval/risk-acceptance fabrication, no ROADMAP parent
auto-check. Exit code:
  0  ready_for_stage2b_completion == true (all groups ready AND all blockers satisfied)
  1  incomplete (default repo state)
  2  the --config file could not be loaded

Stdlib only; delegates to server.build_stage2b_evidence_package_validator.
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
        prog="check_stage2b_evidence_package_validator",
        description="Validate aggregate Stage 2B evidence package readiness (no network, no credentials).")
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
            print(json.dumps({"ready_for_stage2b_completion": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2

    report = server.build_stage2b_evidence_package_validator(config)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ready_for_stage2b_completion: {report['ready_for_stage2b_completion']}  "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for g in report["required_evidence_groups"]:
            mark = "READY  " if report["evidence_group_ready"][g] else "MISSING"
            print(f"  [{mark}] {g}")
        for b in report["blockers"]:
            print(f"  blocker {b['id']} (line {b['roadmap_line']}) satisfied={b['satisfied']}")
    return 0 if report["ready_for_stage2b_completion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
