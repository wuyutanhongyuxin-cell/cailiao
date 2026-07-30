# Stage 2B Evaluation-Run Contract

Defines the shape of a **reproducible, auditable evaluation-run package** for Stage 2B
retrieval/rerank/NLI, aligned with TREC `trec_eval` (qrels + runfile + result snapshot)
and ir-measures / BEIR metric conventions.

**Boundary (read first):** this contract validates the **manifest shape** of an eval run.
A passing manifest proves the run is *declared completely and consistently* — it does
**not** prove the run was executed, that the pointed-to qrels/runfile/result files exist
or contain those numbers, that a provider was reachable, or that the metrics are real.
The validator never reads files at the pointers and never calls a provider. The default
repo has **no real eval run**, and passing this contract does **not** check any real
ROADMAP parent item.

## Eval-run package fields

Identity / dataset:
- `run_id` — stable unique id for the run.
- `created_at` — ISO-like timestamp.
- `dataset_id` — id of the evaluated query set.
- `dataset_readiness_status` — must be `ready_real` for a real run (see
  `summarize_real_query_readiness`).
- `query_count` — 50-100 (the real-set size band).
- `is_template` / template markers — must be **absent/false** for a real run.

Retrieval config versions (summaries, not secrets):
- `config.bm25` — `{k1, b}` (and threshold if used).
- `config.vector` — summary (provider, model, dim, store backend); `credential_source`
  env-var names only, never secret values.
- `config.fusion` — `{method, rank_constant, rank_window_size, channels}`.
- `config.rerank` — summary (provider, model, eval_metrics).
- `config.nli` — summary (provider, model, verdict policy).

Artifact pointers (pointers/hashes only — never inlined file contents):
- `artifacts.qrels` — `{path|uri, sha256}` (TREC qrels).
- `artifacts.runfile` — `{path|uri, sha256}` (ranked runfile).
- `artifacts.result_snapshot` — `{path|uri, sha256}` (computed metrics snapshot).

Metrics (numbers; BEIR/ir-measures style + serving latency):
- `recall@k`, `precision@k`, `ndcg@k`, `mrr`, `map`, `miss_rate`,
  `latency_p50`, `latency_p95`, `refusal_insufficiency_rate`.
- Constraint: `latency_p95 >= latency_p50`.

Verdict:
- `acceptance.verdict` — explicit `pass` / `fail` / `needs_review`.
- `acceptance.rollback_notes` — what to revert to and under what trigger.

## What it proves / does not prove

- **Proves**: the run manifest is complete, internally consistent (metric types,
  latency ordering, query-count band, readiness status, verdict present), and points to
  qrels/runfile/result artifacts by path+hash.
- **Does NOT prove**: that the run actually executed, that the artifact files exist or
  match their hashes, that a provider/model was reached, or that the metric numbers are
  real. Those require running the real pipeline on a real `ready_real` dataset.

## Compact example (placeholder — not a real run)

```json
{
  "run_id": "TEMPLATE-run-0001",
  "is_template": true,
  "created_at": "2026-01-01T00:00:00",
  "dataset_id": "TEMPLATE-dataset",
  "dataset_readiness_status": "template",
  "query_count": 1,
  "config": {"bm25": {"k1": 1.2, "b": 0.75}},
  "artifacts": {"qrels": {"path": "PLACEHOLDER", "sha256": "PLACEHOLDER"}},
  "metrics": {},
  "acceptance": {"verdict": "needs_review"}
}
```

The shipped `examples/stage2b_eval_run.example.json` is deliberately template-marked with
`dataset_readiness_status: "template"`, so it is **invalid / not-ready as a real run** by
default.

## References
- TREC / trec_eval (qrels + runfile + results): https://trec.nist.gov/trec_eval/
- ir-measures (nDCG/MAP/MRR/Recall/Precision): https://ir-measur.es/
- BEIR evaluation benchmark: https://github.com/beir-cellar/beir
- OpenTelemetry (stable run metadata + latency percentiles): https://opentelemetry.io/docs/
