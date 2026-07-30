#!/usr/bin/env python3
"""Unified local + CI quality-gate runner for the cailiao evidence system.

Runs a fixed, ordered set of deterministic gates and reports each gate's status,
duration and a short summary. Designed to be the single entry point both a local
developer and GitHub Actions invoke:

    python tools/run_quality_gates.py            # human-readable
    python tools/run_quality_gates.py --json     # machine-readable (for CI)

Gates (in order):
  1. py-compile     - byte-compile the backend + tests + tools
  2. unittest       - python -m unittest discover -s tests
  3. eval-retrieval - deterministic retrieval quality gate over the anon suite
  4. git-diff       - git diff --check (whitespace/conflict markers); skipped
                      cleanly when not inside a git work tree
  5. secret-scan    - walk the workspace for secret-shaped tokens and .env files

Stdlib only. External commands run via subprocess with shell=False (a fixed argv
list), so no user string is ever passed through a shell. Exit code is 0 only when
no gate failed; a skipped gate does not fail the run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = os.environ.get("PYTHON", "python")
EVAL_SUITE = "tests/data/retrieval_eval_suite.json"

# Directories/suffixes never worth scanning for secrets (noise or binary).
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "data"}
_SKIP_SUFFIXES = {".sqlite3", ".pyc", ".pyo", ".docx", ".xlsx", ".zip", ".png", ".jpg",
                  ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2"}
_MAX_SCAN_BYTES = 1_000_000

# Secret-shaped token patterns. Deliberately written with character classes so
# this source file never itself contains a literal secret-shaped string.
_SECRET_PATTERNS = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "github_pat_classic": re.compile(r"ghp_[A-Za-z0-9]{36}"),
    "github_pat_fine": re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "generic_bearer": re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_.]{20,}"),
}


def _redact(match: str) -> str:
    """Return a non-reversible preview of a matched token (never the full value)."""
    head = match[:4]
    return f"{head}...(len={len(match)})"


def scan_text_for_secrets(text: str) -> list[dict[str, str]]:
    """Return redacted findings for any secret-shaped tokens in ``text``."""
    findings: list[dict[str, str]] = []
    for kind, pattern in _SECRET_PATTERNS.items():
        for m in pattern.findall(text or ""):
            token = m if isinstance(m, str) else m[0]
            findings.append({"kind": kind, "preview": _redact(token)})
    return findings


def is_env_file(name: str) -> bool:
    """True for private .env / .env.local style files, excluding templates.

    Files such as .env.example and .env.sample are repository documentation and
    should be scanned as normal text rather than treated as private env files.
    """
    if name in {".env.example", ".env.sample", ".env.template"}:
        return False
    return name == ".env" or name.startswith(".env.")


def _in_skipped_dir(rel: Path) -> bool:
    return any(part in _SKIP_DIRS for part in rel.parts)


def find_env_files(root: Path) -> list[str]:
    """Return workspace-relative paths of any .env* files (contents never read)."""
    found: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _in_skipped_dir(rel.parent):
            continue
        if is_env_file(path.name):
            found.append(str(rel))
    return sorted(found)


def scan_workspace_for_secrets(root: Path) -> dict[str, object]:
    """Walk the workspace for secret-shaped tokens and .env files.

    .env* files are reported by path only and never opened, honoring the privacy
    boundary. Other text files are scanned and any hits are redacted.
    """
    secret_findings: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _in_skipped_dir(rel.parent):
            continue
        if is_env_file(path.name):
            continue  # never read .env* contents
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable or binary -> skip
        for finding in scan_text_for_secrets(text):
            secret_findings.append({**finding, "path": str(rel)})
    return {"secret_findings": secret_findings, "env_files": find_env_files(root)}


def _git_tracked_env_files(root: Path) -> list[str]:
    """.env* files that are actually tracked by git (a real leak risk)."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=str(root), shell=False,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return sorted(f for f in out.stdout.splitlines() if is_env_file(Path(f).name))


def _inside_git_work_tree(root: Path) -> bool:
    try:
        out = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                             cwd=str(root), shell=False, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def _run_cmd(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a fixed argv (shell=False); return (returncode, combined tail)."""
    try:
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        proc = subprocess.run(argv, cwd=str(ROOT), shell=False,
                             capture_output=True, text=True, timeout=900,
                             env=child_env)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"failed to launch: {exc}"
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(combined.splitlines()[-15:])
    return proc.returncode, tail


# --- individual gates: each returns (status, summary, returncode) -------------

def _gate_py_compile() -> tuple[str, str, int]:
    with tempfile.TemporaryDirectory(prefix="cailiao-pycache-") as pycache:
        rc, tail = _run_cmd([PY, "-m", "py_compile",
                             "backend/server.py", "tests/test_library.py",
                             "tests/test_claim_insufficiency.py",
                             "tests/test_writing_state.py",
                             "tests/test_structured_writing_plan.py",
                             "tests/test_approved_facts_audit.py",
                             "tests/test_targeted_repair_plan.py",
                             "tests/test_draft_versions.py",
                             "tests/test_unit_template_forbidden.py",
                             "tests/test_docx_style_profile.py",
                             "tests/test_docx_layout_roles.py",
                             "tests/test_docx_structured_fields.py",
                             "tests/test_docx_font_preflight.py",
                             "tests/test_docx_layout_regression.py",
                             "tests/test_benchmark_suite.py",
                             "tests/test_blind_evaluation.py",
                             "tests/test_comparison_baselines.py",
                             "tests/test_outcome_metrics.py",
                             "tests/test_regression_evaluation.py",
                             "tests/test_local_config.py",
                             "tests/test_rbac_workspaces.py",
                             "tests/test_governance.py",
                             "tests/test_provider_risk.py",
                             "tests/test_supply_chain.py",
                             "tests/test_real_query_intake.py",
                             "tests/test_bm25_real_sweep.py",
                             "tests/test_vector_production_readiness.py",
                             "tests/test_rerank_production_readiness.py",
                             "tests/test_vector_pipeline.py",
                             "tests/test_reranker_pipeline.py",
                             "tools/evaluate_retrieval.py", "tools/run_quality_gates.py",
                             "tools/validate_benchmark_suite.py",
                             "tools/validate_blind_eval.py",
                             "tools/validate_comparison_baselines.py",
                             "tools/validate_outcome_metrics.py",
                             "tools/run_regression_evaluation.py",
                             "tools/validate_real_query_set.py",
                             "tools/sweep_bm25_real_queries.py",
                             "tools/check_vector_production_readiness.py",
                             "tools/check_rerank_production_readiness.py"],
                            env={"PYTHONPYCACHEPREFIX": pycache})
    return ("passed" if rc == 0 else "failed",
            "byte-compiled backend/tests/tools" if rc == 0 else tail, rc)


def _gate_unittest() -> tuple[str, str, int]:
    rc, tail = _run_cmd([PY, "-m", "unittest", "discover", "-s", "tests", "-v"])
    return ("passed" if rc == 0 else "failed", tail, rc)


def _gate_eval_retrieval() -> tuple[str, str, int]:
    rc, tail = _run_cmd([PY, "backend/server.py", "eval-retrieval",
                         "--suite", EVAL_SUITE, "--k", "10",
                         "--min-title-recall", "0.8", "--min-chunk-recall", "1.0",
                         "--max-misses", "2"])
    return ("passed" if rc == 0 else "failed",
            "retrieval quality gate met thresholds" if rc == 0 else tail, rc)


def _gate_git_diff(skip: bool) -> tuple[str, str, int]:
    if skip:
        return "skipped", "git-diff skipped via --skip-git-diff", 0
    if not _inside_git_work_tree(ROOT):
        return "skipped", "not inside a git work tree", 0
    rc, tail = _run_cmd(["git", "diff", "--check"])
    return ("passed" if rc == 0 else "failed",
            "no whitespace/conflict issues" if rc == 0 else tail, rc)


def _gate_secret_scan() -> tuple[str, str, int]:
    result = scan_workspace_for_secrets(ROOT)
    secrets = result["secret_findings"]
    tracked_env = _git_tracked_env_files(ROOT) if _inside_git_work_tree(ROOT) else []
    env_files = result["env_files"]
    problems = []
    if secrets:
        problems.append(f"{len(secrets)} secret-shaped token(s): "
                        + ", ".join(f"{s['path']}({s['kind']}:{s['preview']})" for s in secrets[:5]))
    if tracked_env:
        problems.append("tracked .env file(s): " + ", ".join(tracked_env))
    if problems:
        return "failed", "; ".join(problems), 1
    note = "no secret-shaped tokens; "
    note += f"{len(env_files)} local .env file(s) present (not read)" if env_files else "no .env files"
    return "passed", note, 0


ALL_GATES = ["py-compile", "unittest", "eval-retrieval", "git-diff", "secret-scan"]


def run_gates(only: list[str] | None = None, skip_git_diff: bool = False) -> dict[str, object]:
    """Run the selected gates and return a JSON-serializable report."""
    selected = [g for g in ALL_GATES if not only or g in only]
    results = []
    for name in selected:
        start = time.monotonic()
        if name == "py-compile":
            status, summary, rc = _gate_py_compile()
        elif name == "unittest":
            status, summary, rc = _gate_unittest()
        elif name == "eval-retrieval":
            status, summary, rc = _gate_eval_retrieval()
        elif name == "git-diff":
            status, summary, rc = _gate_git_diff(skip_git_diff)
        elif name == "secret-scan":
            status, summary, rc = _gate_secret_scan()
        else:  # pragma: no cover - guarded by selection
            continue
        results.append({
            "gate": name,
            "status": status,
            "returncode": rc,
            "duration_sec": round(time.monotonic() - start, 3),
            "summary": summary,
        })
    passed = all(r["status"] != "failed" for r in results)
    return {
        "passed": passed,
        "gate_count": len(results),
        "failed_gates": [r["gate"] for r in results if r["status"] == "failed"],
        "skipped_gates": [r["gate"] for r in results if r["status"] == "skipped"],
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_quality_gates",
        description="Run the unified local/CI quality gates for the cailiao system.")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument("--skip-git-diff", action="store_true",
                        help="skip the git diff --check gate")
    parser.add_argument("--only", action="append", default=None,
                        help="run only this gate (repeatable)")
    args = parser.parse_args(argv)

    report = run_gates(only=args.only, skip_git_diff=args.skip_git_diff)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in report["results"]:
            mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[r["status"]]
            print(f"[{mark}] {r['gate']:<14} ({r['duration_sec']}s)  {r['summary'].splitlines()[0] if r['summary'] else ''}")
        print(f"\n{'ALL GATES PASSED' if report['passed'] else 'QUALITY GATES FAILED: ' + ', '.join(report['failed_gates'])}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
