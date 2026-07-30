# Stage 2B Reranker Rollout Protocol

This protocol defines the metadata packet required before enabling a real
reranker/cross-encoder provider and deepening RRF fusion.

Boundary: this repository still ships a deterministic local reranker only
(`is_real_rerank_model=false`). This protocol does not call providers, download
models, run evaluations, read `.env`, or verify real metrics.

## Required Packet

- `rerank_config`: real provider/model declaration, `credential_source` env var
  name, and eval metrics.
- `candidate_policy`: `top_k`, `rerank_only_fused_top_k=true`, and
  `may_retrieve_new_chunks=false`.
- `rrf_policy`: channels, `rank_constant`, `rank_window_size`, and tie policy.
- `eval_packet`: ready-real dataset status, run manifest reference, and required
  metrics: `mrr`, `ndcg`, `map`, `recall@k`, `latency_p95`.
- `observability`: latency p50/p95, provider error rate, and rerank invocation
  counts.
- `rollout`: preflight checklist, canary steps, rollback steps, rollback trigger.

The reranker must only reorder already fused Top-K candidates. It must not fetch
new chunks or expand recall. Retrieval recall belongs to BM25/vector channels;
rerank affects ordering and precision of the candidate set.

## RRF Notes

RRF is rank-based: each channel contributes by rank, not score scale. The rollout
packet records `rank_constant`, `rank_window_size`, channels, and deterministic
tie policy so offline eval and production behavior can be audited.

## Does Not Prove

- Provider credentials work.
- Cross-encoder was called.
- Model quality is sufficient.
- Eval run happened.
- ROADMAP line 107 is complete.

Use:

```powershell
python tools\check_stage2b_rerank_rollout_protocol.py --json
python tools\check_stage2b_rerank_rollout_protocol.py --config examples\stage2b_rerank_rollout.example.json --json
```

Default and example packets exit non-zero because no real rollout packet is
present and the example is marked `is_template`.
