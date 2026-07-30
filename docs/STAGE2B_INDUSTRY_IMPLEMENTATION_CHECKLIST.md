# Stage 2B Industry Implementation Checklist

Status: industry-aligned implementation checklist only.

This checklist converts web-reviewed references into concrete steps, evidence,
quality gates, observability requirements, and rollback/human-review rules for
the five unresolved Stage 2B blockers. It does not fetch references at runtime,
call providers, run evals, create telemetry, read credentials, accept risk, or
mark ROADMAP parent items complete.

Machine-readable status:

- `build_stage2b_industry_implementation_checklist(config=None)`
- `tools/check_stage2b_industry_implementation_checklist.py`

## Checklist

| Blocker | ROADMAP line | Core method | Default status |
| --- | ---: | --- | --- |
| `real_query_set` | 97 | NIST-governed provenance + BEIR-style qrels | `blocked_by_external_input` |
| `real_query_bm25_calibration` | 100 | BEIR-style retrieval eval metrics and sweep manifest | `blocked_by_external_input` |
| `real_embedding_provider_vector_store` | 103 | hybrid dense/sparse retrieval with persistent index and telemetry | `blocked_by_external_input` |
| `real_reranker_rrf` | 107 | retrieve-rerank CrossEncoder pattern + RRF controls | `blocked_by_external_input` |
| `real_nli_semantic_conflict` | 114 | FEVER/SNLI/CFEVER-style support/refute/NEI and NLI labels | `blocked_by_external_input` |

## References

- NIST AI RMF / AIRC: https://www.nist.gov/itl/ai-risk-management-framework
- BEIR: https://github.com/beir-cellar/beir
- BEIR paper: https://huggingface.co/papers/2104.08663
- Elasticsearch reciprocal rank fusion: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
- Qdrant hybrid queries: https://qdrant.tech/documentation/search/hybrid-queries/
- SentenceTransformers retrieve-rerank: https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html
- OpenTelemetry semantic conventions: https://opentelemetry.io/docs/specs/semconv/
- FEVER: https://fever.ai/dataset/fever.html
- SNLI: https://nlp.stanford.edu/projects/snli/
- CFEVER: https://ojs.aaai.org/index.php/AAAI/article/view/29825

## Boundary

Checklist alignment is not production proof. Passing this checklist only proves
that implementation requirements are documented; it does not prove data quality,
provider reachability, model inference, persistent indexing, telemetry truth,
risk acceptance, or metric validity.
