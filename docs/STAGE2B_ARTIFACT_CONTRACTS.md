# Stage 2B Artifact Contracts

Machine-readable contracts for the real-world inputs Stage 2B needs. These describe
the **declared artifact shape** a human/provider supplies under `config.artifacts`
so it can be validated in a standards-aligned, auditable way.

**Boundary (read first):** these contracts validate **metadata / data shape only**.
Passing a contract proves the *declared config is complete and well-formed* — it does
**not** prove a real provider was contacted, a credential is valid, an index/model was
built, or any evaluation was run. Credential fields carry **environment-variable names**
(`credential_source`), never secret values. Satisfying every contract does **not** check
any real ROADMAP parent item; those stay unchecked until real data/providers are
integrated and evaluated. See `build_external_dependency_audit` /
`build_stage2b_production_playbook_status` for the aggregate gate.

Each artifact key is checked by an existing readiness helper (single source of truth):

## `real_query_set`
- **Required fields**: `metadata` (name), `cases[]` each with `id`, `query`,
  `provenance{source, collected_at, anonymized: true}`, and ≥1 relevance target
  (`relevant_titles` / `relevant_chunk_ids` / `relevant_chunk_markers`); 50-100 cases;
  no placeholder/synthetic markers.
- **Forbidden fields**: PII/secret field names (`name`, `phone`, `email`, `id_card`,
  `身份证`, `api_key`, …) and PII-shaped values in text.
- **Validator**: `summarize_real_query_readiness` (status must be `ready_real`).
- **Proves**: the declared set is shaped like a real anonymized eval set with provenance.
- **Does NOT prove**: that the queries are genuinely real or truly de-identified.

## `real_query_bm25_calibration`
- **Required fields**: everything in `real_query_set` **plus** a non-empty `corpus[]`
  (docs with `title`/`text`) to sweep against.
- **Forbidden fields**: same PII/secret restrictions as `real_query_set`.
- **Validator**: `summarize_real_query_readiness` == `ready_real` **and** a corpus present
  (as gated by `run_bm25_sweep_on_real_query_set`).
- **Proves**: a calibration run *could* execute on the declared set + corpus.
- **Does NOT prove**: that calibration was run or that k1/b/threshold were tuned on real data.

## `real_embedding_provider_vector_store`
- **Required fields**: `provider{provider, model, dim, credential_source}` (real, non-test),
  `store{backend}` a persistent backend (`postgres_pgvector`/`qdrant`/`milvus`/…),
  `index{metric|type}`.
- **Forbidden fields**: `api_key`/`key`/`secret`/`password`/`token`/`authorization`/`endpoint_url`
  (use `credential_source` env-var name instead).
- **Validator**: `build_vector_index_readiness` (`production_ready == true`).
- **Proves**: the declared provider+store+index config is complete.
- **Does NOT prove**: the provider is reachable, the credential is valid, or an index exists.

## `real_reranker_rrf`
- **Required fields**: `provider{provider, model, credential_source, eval_metrics[]}` (metrics
  from mrr/ndcg/map), `rrf{rank_constant≥1, rank_window_size≥1, channels[]}`.
- **Forbidden fields**: same credential-value fields as above.
- **Validator**: `build_rerank_pipeline_readiness` (`production_ready == true`).
- **Proves**: a real cross-encoder + RRF fusion config is completely declared.
- **Does NOT prove**: the reranker was called, evaluated, or that latency/quality were measured.

## `real_nli_semantic_conflict`
- **Required fields**: `provider{provider, model, credential_source}` (real NLI/LLM),
  `eval_labels[]` covering supports/refutes/not_enough_info (SNLI/FEVER/CFEVER spellings),
  `policy{verdict_labels, min_confidence∈[0,1], block_on, warn_on}`.
- **Forbidden fields**: same credential-value fields as above.
- **Validator**: `build_semantic_conflict_readiness` (`production_ready == true`).
- **Proves**: a real NLI provider + eval labels + decision policy are completely declared.
- **Does NOT prove**: entailment/contradiction inference ran or was evaluated.

## Compact example

```json
{
  "artifacts": {
    "real_query_set": {
      "metadata": {"name": "TEMPLATE real set (fill me)", "is_template": true},
      "cases": [
        {"id": "q001", "query": "示例占位查询",
         "provenance": {"source": "template", "collected_at": "2026-01-01T00:00:00", "anonymized": true},
         "relevant_titles": ["示例相关标题"]}
      ]
    },
    "real_embedding_provider_vector_store": {
      "provider": {"provider": "openai", "model": "text-embedding-3-large", "dim": 3072,
                   "credential_source": "OPENAI_API_KEY"},
      "store": {"backend": "qdrant", "backup_configured": true},
      "index": {"metric": "cosine", "type": "hnsw"}
    }
  }
}
```

The shipped `examples/stage2b_artifacts.example.json` is **placeholder-only**: it shows the
shape but is deliberately marked as a template so it does **not** satisfy real readiness
by default (a placeholder-marked query set can never be `ready_real`).

## References
- Elasticsearch RRF: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
- Qdrant hybrid queries: https://qdrant.tech/documentation/search/hybrid-queries/
- SBERT retrieve & re-rank: https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html
- BEIR evaluation: https://github.com/beir-cellar/beir
