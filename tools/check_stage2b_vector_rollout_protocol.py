#!/usr/bin/env python3
"""Validate a Stage 2B vector provider/store/index rollout packet.

Usage:
    python tools/check_stage2b_vector_rollout_protocol.py --json
    python tools/check_stage2b_vector_rollout_protocol.py --config path/to/packet.json --json

Validates declared metadata only. It never calls an embedding provider, opens a
vector DB, reads credentials, builds an index, or verifies real metrics.
Exit code:
  0  rollout packet validates (declared shape only)
  1  no packet / packet does not validate
  2  config load error
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
        prog="check_stage2b_vector_rollout_protocol",
        description="Validate vector rollout packet shape (no provider, no DB, no secrets).")
    parser.add_argument("--config", default=None, help="optional path to a vector rollout packet JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ready_for_vector_rollout": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2
        config = loaded if isinstance(loaded, dict) and "vector_rollout" in loaded else {"vector_rollout": loaded}

    report = server.build_stage2b_vector_rollout_protocol(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ready_for_vector_rollout: {report['ready_for_vector_rollout']}  "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for e in report["validation"]["errors"]:
            print(f"  ERROR: {e}")
        for w in report["validation"]["warnings"]:
            print(f"  WARN:  {w}")
    return 0 if report["ready_for_vector_rollout"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
