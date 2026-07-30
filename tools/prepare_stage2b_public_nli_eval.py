"""Prepare or validate a local public NLI semantic eval dataset.

This tool never downloads data and never calls an NLI/LLM provider.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "backend" / "server.py"

spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def _load_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/validate local public NLI semantic eval intake.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--input", required=True, help="Local JSON/JSONL records. No download is performed.")
    build.add_argument("--output", required=True)
    build.add_argument("--dataset-type", default="nli_jsonl", choices=sorted(server.PUBLIC_NLI_DATASET_TYPES))
    build.add_argument("--source-url", required=True)
    build.add_argument("--license", required=True)
    build.add_argument("--template", action="store_true", help="Mark output as template/example; validation will fail.")

    validate = sub.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.add_argument("--min-cases", type=int, default=3)
    validate.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "build":
        records = server.load_public_nli_eval_records(args.input)
        dataset = server.build_public_nli_semantic_eval_dataset(records, {
            "dataset_type": args.dataset_type,
            "source_url": args.source_url,
            "license": args.license,
            "is_template": bool(args.template),
        })
        Path(args.output).write_text(json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
        result = server.validate_public_nli_semantic_eval_dataset(dataset)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1

    dataset = _load_json(args.input)
    result = server.validate_public_nli_semantic_eval_dataset(dataset, min_cases=args.min_cases)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("passed" if result["passed"] else "failed")
        for err in result["errors"]:
            print(f"error: {err}", file=sys.stderr)
        for warn in result["warnings"]:
            print(f"warning: {warn}", file=sys.stderr)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
