# Stage 2B Observability Contract

Status: telemetry contract only.

This contract names the metrics, dimensions, golden-signal mappings, and
alert/rollback signals expected before Stage 2B external blockers can be promoted.
It follows OpenTelemetry-style metrics/traces, SRE golden signals, and NIST AI RMF
monitoring discipline. It does not emit telemetry, create dashboards, run evals,
call providers, or mark ROADMAP parents complete.

Machine-readable status:

- `build_stage2b_observability_contract(config=None)`
- `tools/check_stage2b_observability_contract.py`
- `examples/stage2b_observability_contract.example.json`

## Contract

| Blocker | ROADMAP line | Required telemetry | Alert / rollback |
| --- | ---: | --- | --- |
| `real_query_set` | 97 | intake count, PII rejection count, qrels coverage | PII-shaped value, placeholder marker, provenance/qrels gap |
| `real_query_bm25_calibration` | 100 | sweep runs, `recall@10`, MRR, miss rate | recall/MRR regression or miss-rate increase |
| `real_embedding_provider_vector_store` | 103 | retrieval latency p50/p95, provider error rate, index status, `recall@10`, `nDCG@10` | p95 breach, error spike, index mismatch, retrieval regression |
| `real_reranker_rrf` | 107 | rerank latency p95, invocation/error rate, MRR, nDCG, MAP | no quality lift, p95 breach, RRF drift, error spike |
| `real_nli_semantic_conflict` | 114 | NLI latency p95, verdict distribution, per-label F1, fabrication count, review queue depth | fabrication, missed refutation, calibration drift, review queue breach |

## Boundary

Observability contract readiness is not production proof. The repository must
receive real telemetry snapshots and eval reports before any parent item can be
considered complete.
