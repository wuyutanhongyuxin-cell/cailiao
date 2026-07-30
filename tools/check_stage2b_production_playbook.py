#!/usr/bin/env python3
"""Report Stage 2B production-playbook readiness (planning matrix, gated).

Usage:
    python tools/check_stage2b_production_playbook.py --json
    python tools/check_stage2b_production_playbook.py --config path/to/artifacts.json --json

Emits the Stage 2B rollout phases, required BEIR-style metrics, acceptance gates,
and references, and whether the repo is ready_for_real_provider_rollout. This
mirrors the external-dependency audit: a phase is only "ready" when its blocker is
satisfied by DECLARED metadata shape; no provider is contacted, no credential is
read, and no ROADMAP parent item is auto-checked. Exit code:
  0  ready_for_real_provider_rollout == true (declared metadata only)
  1  not ready (default repo state)
  2  the --config file could not be loaded

Stdlib only; delegates to server.build_stage2b_production_playbook_status.
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
        prog="check_stage2b_production_playbook",
        description="Report Stage 2B production-playbook readiness (no network, no credentials).")
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
            print(json.dumps({"ready_for_real_provider_rollout": False,
                              "error": f"failed to load config: {exc}"}, ensure_ascii=False, indent=2))
            return 2

    report = server.build_stage2b_production_playbook_status(config)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ready_for_real_provider_rollout: {report['ready_for_real_provider_rollout']}  "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for p in report["phases"]:
            mark = "READY  " if p["ready"] else "PENDING"
            print(f"  [{mark}] {p['step']}. {p['name']} (blocker={p['blocker_id']})")
    return 0 if report["ready_for_real_provider_rollout"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

