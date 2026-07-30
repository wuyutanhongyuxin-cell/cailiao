# Final Completion Blocker Audit

Status: completion boundary only.

This audit records whether the repository has any honest repo-only work remaining
and whether project completion is still blocked by external evidence. It does not
create external evidence, accept risk, run evals, call providers, read credentials,
or mark ROADMAP parent items complete.

Machine-readable status:

- `build_final_completion_blocker_audit(config=None)`
- `tools/check_final_completion_blocker_audit.py`

Default result:

- `project_complete=false`
- `repo_only_work_remaining=false`
- `blocked_by_external_input=true`
- `roadmap_parent_items_checked=false`

## External Blockers

| Blocker | ROADMAP line | Default status |
| --- | ---: | --- |
| `real_query_set` | 97 | `blocked_by_external_input` |
| `real_query_bm25_calibration` | 100 | `blocked_by_external_input` |
| `real_embedding_provider_vector_store` | 103 | `blocked_by_external_input` |
| `real_reranker_rrf` | 107 | `blocked_by_external_input` |
| `real_nli_semantic_conflict` | 114 | `blocked_by_external_input` |

Completion can only be claimed after the real external evidence exists and is
reviewed by an accountable human owner.
