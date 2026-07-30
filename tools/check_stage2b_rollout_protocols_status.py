#!/usr/bin/env python3
"""Aggregate Stage 2B rollout protocol readiness.

Metadata only. No provider calls, DB connections, model downloads, eval runs,
credential reads, or ROADMAP parent completion.
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
        prog="check_stage2b_rollout_protocols_status",
        description="Aggregate vector/rerank/NLI rollout protocol readiness (metadata only).")
    parser.add_argument("--config", default=None, help="optional rollout_protocols config JSON")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"all_rollout_protocols_ready": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2

    report = server.build_stage2b_rollout_protocols_status(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"all_rollout_protocols_ready: {report['all_rollout_protocols_ready']} "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for pid in report["outstanding_ids"]:
            print(f"  OUTSTANDING: {pid}")
    return 0 if report["all_rollout_protocols_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
