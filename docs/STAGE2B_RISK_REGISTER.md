# Stage 2B Risk Register

Status: risk register / treatment plan only.

This register records default risks, impacts, treatment plans, owner roles, and
evidence required to close the five Stage 2B external blockers. It aligns with
NIST AI RMF risk management and ISO 31000-style risk treatment. It does not accept
risks, run evals, call providers, or mark ROADMAP parents complete.

Machine-readable status:

- `build_stage2b_risk_register(config=None)`
- `tools/check_stage2b_risk_register.py`
- `examples/stage2b_risk_register.example.json`

## Register

| Blocker | ROADMAP line | Default status | Owner |
| --- | ---: | --- | --- |
| `real_query_set` | 97 | `open_external_risk` | `evaluation_dataset_owner` |
| `real_query_bm25_calibration` | 100 | `open_external_risk` | `retrieval_quality_owner` |
| `real_embedding_provider_vector_store` | 103 | `open_external_risk` | `retrieval_platform_owner` |
| `real_reranker_rrf` | 107 | `open_external_risk` | `ranking_quality_owner` |
| `real_nli_semantic_conflict` | 114 | `open_external_risk` | `semantic_safety_owner` |

## Boundary

Risk treatment is not risk acceptance. Real evidence and accountable approval must
exist before parent items can be considered complete.
