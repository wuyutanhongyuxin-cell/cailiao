"""Final delivery slice 4: minimal MaterialTask frontend workspace (v1) tests.

Static assertions over frontend/index.html and frontend/app.js: the "任务" nav +
panel exist with all required stable ids, the app defines the task
create/list/load/save/search/attach/approve/generate/audit/preflight/export
functions, references every task backend route, uses escapeHtml in task render
paths, has a GET helper separate from POST, guards the no-task case, and frames
task evidence approval as manual/deterministic (never semantic entailment).

Static-only (no server / no browser). This does not exercise runtime behavior.
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")


class FrontendTaskUiTest(unittest.TestCase):
    REQUIRED_IDS = [
        "taskCreateBtn", "taskRefreshBtn", "taskList", "taskCurrentId",
        "taskLoadCurrentBtn", "taskSaveCurrentBtn", "taskEvidenceQuery",
        "taskEvidenceSearchBtn", "taskEvidenceResults", "taskAttachManualBtn",
        "taskApproveSelectedBtn", "taskGenerateBtn", "taskAuditBtn",
        "taskPreflightBtn", "taskExportDocxBtn", "taskStatus", "taskLog",
    ]

    def test_index_html_has_task_nav(self):
        self.assertIn('data-panel="tasks"', INDEX_HTML)
        self.assertIn('id="tasks"', INDEX_HTML)

    def test_index_html_has_all_required_ids(self):
        for el_id in self.REQUIRED_IDS:
            self.assertIn(f'id="{el_id}"', INDEX_HTML,
                          f"index.html missing element id={el_id}")

    def test_app_js_defines_task_functions(self):
        for fn in ("createTask", "refreshTasks", "renderTaskList",
                   "loadCurrentTask", "saveCurrentTask", "searchTaskEvidence",
                   "attachSelectedEvidence", "approveTaskEvidence",
                   "generateTask", "auditTask", "preflightTask",
                   "exportTaskDocx", "renderTaskEvidenceResults"):
            self.assertIn(f"function {fn}", APP_JS,
                          f"app.js missing function {fn}")

    def test_app_js_references_all_task_routes(self):
        for route in (
            "/api/tasks",
            "/evidence/search",
            "/evidence/attach",
            "/evidence/approve",
            "/generate",
            "/audit",
            "/export/preflight",
            "/export/docx",
        ):
            self.assertIn(route, APP_JS, f"app.js missing route {route}")

    def test_app_js_uses_put_for_save(self):
        # Save current compose state back to task via PUT /api/tasks/{id}.
        self.assertIn("method: 'PUT'", APP_JS)

    def test_app_js_has_separate_get_helper(self):
        # A GET helper distinct from the existing POST `api`.
        self.assertIn("async function apiGet", APP_JS)

    def test_task_render_paths_use_escape_html(self):
        # Every task renderer must escape server/user text.
        for fn in ("renderTaskList", "renderTaskEvidenceResults",
                   "renderTaskStatus"):
            match = re.search(
                r"function " + fn + r"\b.*?\n\}", APP_JS, re.S)
            self.assertIsNotNone(match, f"could not isolate {fn}")
            self.assertIn("escapeHtml", match.group(0),
                          f"{fn} does not use escapeHtml")

    def test_no_task_case_is_guarded(self):
        # Never assume a task exists; concise status when none selected.
        self.assertIn("未选择任务", APP_JS)
        self.assertIn("未选择任务", INDEX_HTML)

    def test_approval_framed_manual_deterministic_not_semantic(self):
        # Task evidence approval must be described as manual/deterministic and
        # must not claim semantic entailment / truth judgement.
        self.assertIn("人工", APP_JS)
        self.assertTrue("确定性" in INDEX_HTML or "确定性" in APP_JS)
        self.assertTrue("语义蕴含" in INDEX_HTML or "语义蕴含" in APP_JS)
        self.assertTrue("真伪" in INDEX_HTML or "非语义判断" in APP_JS)

    def test_same_origin_relative_routes_only(self):
        # Task calls must be same-origin relative paths (start with /api/…),
        # never an absolute http(s) URL to an external host.
        self.assertIsNone(
            re.search(r"https?://[^\"'`\s]*/api/tasks", APP_JS),
            "task UI must call same-origin /api/tasks, not an absolute URL")


if __name__ == "__main__":
    unittest.main()
