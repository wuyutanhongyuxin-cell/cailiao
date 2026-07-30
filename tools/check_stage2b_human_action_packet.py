#!/usr/bin/env python3
"""Report the Stage 2B human action packet.

Default exit is 1 because the packet represents unresolved real-world inputs unless
a supplied config satisfies all external-dependency blockers by declared metadata
shape. This tool never reads secrets, calls providers, downloads models, or runs
real evaluations.
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
        prog="check_stage2b_human_action_packet",
        description="Report unresolved Stage 2B human/external action items.")
    parser.add_argument("--config", default=None, help="optional declared artifacts config JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"failed to load config: {exc}"}, ensure_ascii=False, indent=2))
            return 2

    server = _load_server()
    report = server.build_stage2b_human_action_packet(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"actions: {report['action_item_count']}  outstanding: "
              f"{len(report['outstanding_action_ids'])}  parents_checked: "
              f"{report['roadmap_parent_items_checked']}")
        for item in report["action_items"]:
            mark = "resolved" if not item["human_action_required"] else "needs-human"
            print(f"  - {item['id']} line {item['roadmap_line']}: {mark}")

    return 0 if report["all_human_actions_resolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
