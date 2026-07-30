#!/usr/bin/env python3
"""Report Stage 2B promotion/SLO gates.

Default exit is 1 until all external blockers have declared proof. Metadata only:
no network, providers, eval execution, or secret reads.
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
        prog="check_stage2b_promotion_gates",
        description="Report Stage 2B promotion/SLO gate policy.")
    parser.add_argument("--config", default=None, help="optional declared artifacts config JSON")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"failed to load config: {exc}"}, ensure_ascii=False, indent=2))
            return 2

    report = _load_server().build_stage2b_promotion_gates(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"gates: {report['gate_count']}  blocked: {len(report['blocked_ids'])}  "
              f"ready: {report['ready_for_promotion']}")
        for gate in report["gates"]:
            print(f"  - {gate['id']} line {gate['roadmap_line']}: {gate['current_status']}")
    return 0 if report["ready_for_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
