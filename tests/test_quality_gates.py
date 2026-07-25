import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "tools" / "run_quality_gates.py"
spec = importlib.util.spec_from_file_location("run_quality_gates", RUNNER)
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)

# Build secret-shaped tokens at runtime via concatenation so this source file
# never itself contains a literal secret-shaped string (which the secret-scan
# gate, run over the whole repo, would otherwise flag).
FAKE_OPENAI = "sk-" + "A" * 32
FAKE_GH_PAT = "ghp_" + "b" * 36
FAKE_GH_FINE = "github_pat_" + "c" * 44


class SecretHelperTest(unittest.TestCase):
    def test_scan_detects_known_shapes(self):
        kinds = {f["kind"] for f in gates.scan_text_for_secrets(
            f"key={FAKE_OPENAI} tok={FAKE_GH_PAT} fine={FAKE_GH_FINE}")}
        self.assertIn("openai_key", kinds)
        self.assertIn("github_pat_classic", kinds)
        self.assertIn("github_pat_fine", kinds)

    def test_findings_are_redacted(self):
        findings = gates.scan_text_for_secrets(f"key={FAKE_OPENAI}")
        self.assertTrue(findings)
        for f in findings:
            self.assertNotIn(FAKE_OPENAI, f["preview"])   # never the full token
            self.assertIn("...", f["preview"])

    def test_clean_text_has_no_findings(self):
        self.assertEqual(gates.scan_text_for_secrets("普通中文正文，无任何密钥。"), [])

    def test_is_env_file(self):
        self.assertTrue(gates.is_env_file(".env"))
        self.assertTrue(gates.is_env_file(".env.local"))
        self.assertTrue(gates.is_env_file(".env.production"))
        self.assertFalse(gates.is_env_file(".env.example"))
        self.assertFalse(gates.is_env_file(".env.sample"))
        self.assertFalse(gates.is_env_file("env"))
        self.assertFalse(gates.is_env_file("server.py"))


class WorkspaceScanTest(unittest.TestCase):
    def test_env_files_listed_but_contents_never_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # A .env holding a secret-shaped token: must be listed, never read.
            (root / ".env").write_text(f"OPENAI_API_KEY={FAKE_OPENAI}\n", encoding="utf-8")
            # A regular file with a real secret-shaped token: must be flagged.
            (root / "leak.txt").write_text(f"token={FAKE_GH_PAT}\n", encoding="utf-8")

            result = gates.scan_workspace_for_secrets(root)
            self.assertIn(".env", result["env_files"])
            paths = {f["path"] for f in result["secret_findings"]}
            self.assertIn("leak.txt", paths)          # regular file flagged
            self.assertNotIn(".env", paths)            # .env never scanned

    def test_skips_excluded_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "x.txt").write_text(f"token={FAKE_GH_PAT}\n", encoding="utf-8")
            result = gates.scan_workspace_for_secrets(root)
            self.assertEqual(result["secret_findings"], [])

    def test_find_env_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text("X=1\n", encoding="utf-8")
            (root / "keep.py").write_text("print(1)\n", encoding="utf-8")
            self.assertEqual(gates.find_env_files(root), [".env"])


class GateRunnerTest(unittest.TestCase):
    def test_all_gates_include_eval_retrieval(self):
        self.assertIn("eval-retrieval", gates.ALL_GATES)
        self.assertEqual(gates.ALL_GATES[0], "py-compile")
        self.assertEqual(gates.ALL_GATES[-1], "secret-scan")
        self.assertEqual(gates.PY, "python")

    def test_git_diff_skip_does_not_fail(self):
        report = gates.run_gates(only=["git-diff"], skip_git_diff=True)
        self.assertTrue(report["passed"])
        self.assertIn("git-diff", report["skipped_gates"])
        self.assertEqual(report["results"][0]["status"], "skipped")

    def test_secret_scan_gate_on_clean_repo(self):
        # The real repository must be free of secret-shaped tokens and tracked .env.
        report = gates.run_gates(only=["secret-scan"])
        self.assertTrue(report["passed"], report["results"])
        self.assertEqual(report["results"][0]["gate"], "secret-scan")

    def test_json_output_structure_and_exit_code(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = gates.main(["--json", "--only", "secret-scan"])
        report = json.loads(buf.getvalue())
        self.assertIn("passed", report)
        self.assertIn("results", report)
        self.assertEqual(report["gate_count"], 1)
        r = report["results"][0]
        for key in ("gate", "status", "returncode", "duration_sec", "summary"):
            self.assertIn(key, r)
        self.assertEqual(code, 0 if report["passed"] else 1)


if __name__ == "__main__":
    unittest.main()
