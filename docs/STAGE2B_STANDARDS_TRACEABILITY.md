# Stage 2B Standards Traceability

Status: standards/reference traceability only.

This matrix links the five unresolved Stage 2B external blockers to industry
references and the evidence humans must provide before ROADMAP parent items can
be completed. It does not fetch references at runtime, call providers, run evals,
read credentials, or mark parent items complete.

Machine-readable status:

- `build_stage2b_standards_traceability(config=None)`
- `tools/check_stage2b_standards_traceability.py`
- `examples/stage2b_standards_traceability.example.json`

## Matrix

| Blocker | ROADMAP line | References | Proof still required |
| --- | ---: | --- | --- |
| `real_query_set` | 97 | NIST AI RMF / GenAI Profile; BEIR | 50-100 anonymized real queries, provenance, qrels/relevance targets |
| `real_query_bm25_calibration` | 100 | BEIR | ready-real query set, corpus snapshot, BM25 sweep manifest and metrics |
| `real_embedding_provider_vector_store` | 103 | NIST AI RMF / GenAI Profile; Qdrant hybrid queries; BEIR | real embedding provider metadata, persistent vector store/index descriptor, production index manifest |
| `real_reranker_rrf` | 107 | SBERT retrieve-rerank; Elasticsearch RRF; Qdrant hybrid queries; BEIR | cross-encoder provider metadata, RRF config, rerank eval quality/latency metrics |
| `real_nli_semantic_conflict` | 114 | NIST AI RMF / GenAI Profile; BEIR | NLI/LLM provider metadata, three-verdict labels, semantic-conflict eval manifest and policy |

## References

- NIST AI RMF / Generative AI Profile: https://www.nist.gov/itl/ai-risk-management-framework
- BEIR: https://github.com/beir-cellar/beir
- Elasticsearch reciprocal rank fusion: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
- Qdrant hybrid queries: https://qdrant.tech/documentation/search/hybrid-queries/
- SentenceTransformers retrieve and rerank: https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html

## Boundary

Traceability is not production proof. Passing this matrix proves reference
alignment is documented; it does not prove data quality, provider reachability,
credential validity, model inference, index construction, or metric truth.
