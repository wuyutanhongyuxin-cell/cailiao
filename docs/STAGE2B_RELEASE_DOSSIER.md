# Stage 2B Release Dossier

Status: release dossier / model-data card contract only.

This dossier defines the records, owner roles, reviewer roles, approval shapes,
and cross-links required before any Stage 2B external blocker can be promoted. It
aligns with AI governance and reporting practices, but does not fabricate
approvals, model cards, dataset cards, evaluations, or production readiness.

Machine-readable status:

- `build_stage2b_release_dossier(config=None)`
- `tools/check_stage2b_release_dossier.py`
- `examples/stage2b_release_dossier.example.json`

## Dossier Items

| Blocker | ROADMAP line | Records | Owner | Reviewer |
| --- | ---: | --- | --- | --- |
| `real_query_set` | 97 | dataset card, anonymization review, qrels coverage, signoff | `evaluation_dataset_owner` | `privacy_reviewer` |
| `real_query_bm25_calibration` | 100 | eval card, sweep manifest, baseline comparison, decision record | `retrieval_quality_owner` | `evaluation_reviewer` |
| `real_embedding_provider_vector_store` | 103 | provider card, index card, rollout record, observability snapshot, security review | `retrieval_platform_owner` | `security_and_operations_reviewer` |
| `real_reranker_rrf` | 107 | model/provider card, rerank eval card, RRF decision record, canary snapshot | `ranking_quality_owner` | `latency_and_quality_reviewer` |
| `real_nli_semantic_conflict` | 114 | model/provider card, semantic eval card, human review policy, fabrication/citation audit, risk signoff | `semantic_safety_owner` | `policy_and_human_review_reviewer` |

## Boundary

Release dossier readiness is not release approval. Real owner/reviewer decisions
and real evidence packages must exist before parent ROADMAP items can be checked.
