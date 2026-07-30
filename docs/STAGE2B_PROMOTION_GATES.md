# Stage 2B Promotion Gates

Status: promotion/SLO policy only.

These gates define what evidence must exist before the five unresolved Stage 2B
parent items can be promoted. They align with measurement, observability, and SLO
discipline, but they do not run measurements or prove production readiness.

Machine-readable status:

- `build_stage2b_promotion_gates(config=None)`
- `tools/check_stage2b_promotion_gates.py`
- `examples/stage2b_promotion_gates.example.json`

## Gates

| Blocker | ROADMAP line | Default gate | Rollback trigger |
| --- | ---: | --- | --- |
| `real_query_set` | 97 | `dataset_readiness_status == ready_real` and query count in `[50,100]` | PII leak, placeholder marker, provenance gap, qrels gap |
| `real_query_bm25_calibration` | 100 | no `recall@10` or MRR regression vs lexical baseline; miss rate not worse | quality regression, unstable threshold, missing corpus snapshot |
| `real_embedding_provider_vector_store` | 103 | hybrid/vector channel preserves or improves `recall@10`/`nDCG@10` within latency/error budget | latency p95 breach, provider error budget burn, index mismatch, retrieval regression |
| `real_reranker_rrf` | 107 | rerank adds precision/MRR lift without latency p95 breach | quality lift absent, latency breach, RRF drift, provider error spike |
| `real_nli_semantic_conflict` | 114 | three-verdict coverage, accepted per-label metrics, fabrication rate 0, citation traceability 100% | unsupported/refuted claim missed, fabrication, low-confidence drift, review backlog breach |

## Boundary

Passing this policy means the repository knows what to require. It does not mean
the real query set exists, BM25 was calibrated, vector/RRF/NLI providers are
reachable, metrics were measured, credentials are valid, or ROADMAP parents are
complete.
