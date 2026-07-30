#!/usr/bin/env python3
"""Validate a Stage 2B reranker/cross-encoder rollout packet.

Usage:
    python tools/check_stage2b_rerank_rollout_protocol.py --json
    python tools/check_stage2b_rerank_rollout_protocol.py --config path/to/packet.json --json

Metadata validation only. No provider call, model download, eval run, credential
read, or ROADMAP parent completion.
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
        prog="check_stage2b_rerank_rollout_protocol",
        description="Validate reranker rollout packet shape (no provider, no model, no secrets).")
    parser.add_argument("--config", default=None, help="optional path to rerank rollout packet JSON")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    server = _load_server()
    config = None
    if args.config:
        try:
            with Path(args.config).open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ready_for_rerank_rollout": False, "error": f"failed to load config: {exc}"},
                             ensure_ascii=False, indent=2))
            return 2
        config = loaded if isinstance(loaded, dict) and "rerank_rollout" in loaded else {"rerank_rollout": loaded}

    report = server.build_stage2b_rerank_rollout_protocol(config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ready_for_rerank_rollout: {report['ready_for_rerank_rollout']} "
              f"(roadmap_parent_items_checked={report['roadmap_parent_items_checked']})")
        for e in report["validation"]["errors"]:
            print(f"  ERROR: {e}")
        for w in report["validation"]["warnings"]:
            print(f"  WARN:  {w}")
    return 0 if report["ready_for_rerank_rollout"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
