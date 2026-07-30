#!/usr/bin/env python3
"""Report Stage 2B industry implementation checklist."""
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
    parser = argparse.ArgumentParser(prog="check_stage2b_industry_implementation_checklist",
                                     description="Report Stage 2B industry implementation checklist.")
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

    report = _load_server().build_stage2b_industry_implementation_checklist(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"checklists: {report['checklist_count']}  ready: {report['ready_for_stage2b_completion']}")
        for section in report["sections"]:
            print(f"  - {section['id']} line {section['roadmap_line']}: {section['current_status']}")
    return 0 if report["ready_for_stage2b_completion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
