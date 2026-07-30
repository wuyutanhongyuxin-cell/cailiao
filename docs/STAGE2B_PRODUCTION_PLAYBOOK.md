# Stage 2B Production Playbook — Hybrid Retrieval + Rerank + Semantic Conflict

Status: **planning playbook only.** This document turns the still-unchecked Stage 2B
real-world items into a concrete, industry-aligned rollout plan. It does not mark
any real parent ROADMAP item complete, and nothing here installs a provider,
downloads a model, reads credentials, or touches the network.

## 1. Current honest state

- **Lexical retrieval**: real. Deterministic BM25/FTS over SQLite is implemented and
  regression-tested; RRF fusion (`1/(rank_constant+rank)`, `RRF_K=60`) is live.
- **Dense vector retrieval**: skeleton only. `DeterministicHashEmbedder` +
  `InProcessVectorIndex` exist behind an opt-in flag and report
  `is_real_embedding_model: false`. No real embedding provider, no persistent store.
- **Reranking**: skeleton only. `DeterministicLocalReranker` re-orders fused Top-K
  and reports `is_real_rerank_model: false`. No real cross-encoder.
- **Semantic conflict / citation entailment**: lexical only. `detect_conflict_evidence`
  is deterministic word-level; there is no NLI/LLM entailment inference.
- **Real evaluation set**: absent. Only a fill-in template + intake scaffold exist;
  there is no `ready_real` 50-100 case anonymized query set.

The five external blockers are enumerated machine-readably by
`build_external_dependency_audit` and mirrored by
`build_stage2b_production_playbook_status`.

## 2. Target production architecture

Retrieve-then-rerank hybrid pipeline (aligned with Elasticsearch RRF, Qdrant hybrid
queries, and Sentence-Transformers retrieve-&-rerank):

1. **Lexical child retriever** — BM25/FTS top-K (already real here).
2. **Dense child retriever** — a real embedding provider + persistent vector store
   (e.g. pgvector / Qdrant / Milvus) producing dense top-K; optional **sparse**
   (learned-sparse) child retriever for a third signal.
3. **Fusion** — rank-based **RRF** with explicit `rank_constant` and
   `rank_window_size`; consider **weighted RRF** or **DBSF** and pick the fusion
   method empirically on the real eval set (Qdrant documents choosing per eval).
4. **Cross-encoder rerank** — a CrossEncoder scores **only the fused top-K candidate
   pairs** (never re-retrieves), reordering for final precision.
5. **Citation entailment / semantic conflict** — an NLI/LLM classifies
   claim↔evidence as supports / refutes / not_enough_info (SNLI/FEVER/CFEVER label
   space) to gate citations and surface real (not just lexical) conflicts.

Credential/security boundary throughout: configs name **environment variables**
(`credential_source`), never secret values; the audit/readiness layer resolves no
secret and contacts no provider.

## 3. Concrete rollout sequence

1. **Collect a ready_real 50-100 query set** — human-supplied, anonymized, with
   provenance; validate with `summarize_real_query_readiness` until status is
   `ready_real`.
2. **Calibrate BM25 on the real set** — run `run_bm25_sweep_on_real_query_set`
   (gated: refuses non-`ready_real`/corpus-less input) to pick `k1`/`b`/threshold.
3. **Choose a vector store/provider and build a persistent index** — declare it via
   the vector-readiness config (provider + `credential_source`, persistent store,
   index descriptor); confirm with `build_vector_index_readiness`.
4. **Run hybrid eval and tune fusion** — evaluate lexical+dense (+sparse) under RRF /
   weighted RRF / DBSF; select `rank_constant`/`rank_window_size` and method on the
   real set.
5. **Add the cross-encoder reranker and evaluate top-K latency/quality** — rerank the
   fused top-K only; measure quality lift and latency; confirm with
   `build_rerank_pipeline_readiness`.
6. **Add NLI semantic conflict + evidence entailment evaluation** — classify
   claim↔evidence, evaluate against labeled data; confirm with
   `build_semantic_conflict_readiness`.

## 4. Evaluation metrics

Per BEIR-style retrieval evaluation, report on the real set:

- **Recall@k**, **Precision@k**
- **nDCG@k**
- **MRR**
- **MAP**
- **miss rate**
- **latency p50 / p95** (retrieval, rerank, end-to-end)
- **refusal / insufficiency rate** (how often the system correctly withholds/flags)

Persist runfiles/result snapshots so every number is traceable to a rule/retrieval/
model/template version (BEIR runfile pattern).

## 5. Acceptance gates and rollback criteria

Promote a stage only when, on the real eval set:

- Retrieval quality does not regress vs the current lexical baseline (nDCG@10 and
  Recall@10 ≥ baseline; miss rate ≤ baseline).
- Rerank adds measurable precision lift without breaching the latency p95 budget.
- Citation entailment reaches the agreed supports/refutes/not_enough_info accuracy
  on labeled data, with key-fact fabrication rate 0 and citation traceability 100%.

**Rollback** if any promoted stage later shows: a quality regression beyond the gate,
a latency p95 breach, a rise in fabrication/insufficiency, or a provider/credential
incident. Each stage is opt-in and independently revertible to the deterministic
skeleton.

## 6. References

- Elasticsearch Reciprocal Rank Fusion: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
- Qdrant hybrid / multi-stage queries: https://qdrant.tech/documentation/search/hybrid-queries/
- Sentence-Transformers retrieve & re-rank: https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html
- BEIR evaluation benchmark: https://github.com/beir-cellar/beir
