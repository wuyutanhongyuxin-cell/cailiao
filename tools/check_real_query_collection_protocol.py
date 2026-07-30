#!/usr/bin/env python3
"""Validate a real-query collection packet against the collection protocol.

Usage:
    python tools/check_real_query_collection_protocol.py --json
    python tools/check_real_query_collection_protocol.py --config path/to/packet.json --json

Validates a declared collection packet's metadata + de-identification checklist
(collector role, purpose, retention policy, access-control summary, all checklist
items true, target_count in 50-100, and no PII-shaped values in any sample cases).
It collects no data, reads no secret files, and never contacts a network. The
default repo state has no real collection packet. Exit code:
  0  a complete, non-template packet validates (declared metadata/checklist only)
  1  no packet / packet not ready (default repo state)
  2  the --config file could not be loaded

The packet may be passed as the top-level object or under a "collection_packet"
key. Stdlib only; delegates to server.build_real_query_collection_protocol /
validate_real_query_collection_packet.
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
        prog="check_real_query_collection_protocol",
        description="Validate a real-query collection packet (no data collection, no network).")
    parser.add_argument("--config", default=None, help="optional path to a collection packet JSON")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ready_for_collection": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2
        config = loaded if isinstance(loaded, dict) and "collection_packet" in loaded \
            else {"collection_packet": loaded}

    report = server.build_real_query_collection_protocol(config)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ready_for_collection: {report['ready_for_collection']}  "
              f"contains_real_queries: {report['contains_real_queries']}  "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for e in report["validation"]["errors"]:
            print(f"  ERROR: {e}")
        for w in report["validation"]["warnings"]:
            print(f"  WARN:  {w}")
    return 0 if report["ready_for_collection"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
