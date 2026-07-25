import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")


class FrontendSearchUiTest(unittest.TestCase):
    """Assert the Library search/audit UI is actually wired (ids + renderers)."""

    REQUIRED_IDS = [
        "libSearchQuery", "libSearchAuthority", "libSearchSourceType",
        "libSearchRegion", "libSearchOrganization", "libSearchDateFrom",
        "libSearchDateTo", "libSearchFormat", "libSearchEffectiveOnly",
        "libClaim", "searchBtn", "verifyClaimBtn", "searchMsg",
        "searchResults", "libSearch",
    ]

    def test_index_html_has_all_required_ids(self):
        for el_id in self.REQUIRED_IDS:
            self.assertIn(f'id="{el_id}"', INDEX_HTML,
                          f"index.html missing element id={el_id}")

    def test_index_html_has_search_tab(self):
        self.assertIn('data-lib="search"', INDEX_HTML)

    def test_app_js_defines_renderers(self):
        self.assertIn("function renderSearch", APP_JS)
        self.assertIn("function verifyClaim", APP_JS)

    def test_search_renderer_surfaces_retrieval_detail(self):
        for token in ("fused_score", "hit_reasons", "channels",
                      "vector", "bm25", "location_kind"):
            self.assertIn(token, APP_JS, f"renderSearch does not surface {token}")

    def test_search_filters_include_metadata_v1_controls(self):
        for token in ("organization", "format", "date_from", "date_to",
                      "effective_only", "activeFilterSummary", "生效过滤",
                      "先过滤候选"):
            self.assertIn(token, APP_JS)

    def test_verify_renderer_surfaces_evidence_map(self):
        for token in ("evidence_map", "covered_markers", "missing_markers",
                      "required_markers", "coverage_ratio", "supporting_items",
                      "matched_markers", "matched_terms", "cited_chunk_ids"):
            self.assertIn(token, APP_JS, f"verifyClaim does not surface {token}")

    def test_no_mojibake_question_mark_runs(self):
        # The old renderers had runs like '???' from mis-decoded Chinese; ensure
        # none remain in the displayed strings.
        self.assertIsNone(re.search(r"\?{3,}", APP_JS),
                          "app.js still contains mojibake '???' runs")

    def test_ui_states_lexical_not_semantic(self):
        # UI must not claim semantic entailment; it must say lexical coverage.
        self.assertIn("词面覆盖", APP_JS)
        self.assertIn("语义", APP_JS)  # e.g. 需人工语义复核 / 不代表语义蕴含

    def test_empty_input_guard_present(self):
        # Both renderers must guard empty query/claim before calling the API.
        self.assertIn("请输入检索查询", APP_JS)
        self.assertIn("请输入需要核验的主张", APP_JS)

    def test_uses_escape_html(self):
        # Server/user content must be escaped in the new renderers.
        self.assertIn("escapeHtml", APP_JS)


if __name__ == "__main__":
    unittest.main()
