#!/usr/bin/env python3
"""Thin CLI wrapper around backend/server.py's eval-retrieval quality gate.

Usage:
    python tools/evaluate_retrieval.py --suite tests/data/retrieval_eval_suite.json --k 10 \
        --min-title-recall 0.8 --min-chunk-recall 1.0 --max-misses 2

This delegates entirely to server.eval_retrieval_cli so there is a single source
of truth for flags, thresholds and exit codes. Stdlib only; no external deps.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    server = _load_server()
    return server.eval_retrieval_cli(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
