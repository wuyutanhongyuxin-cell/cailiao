# Stage 2B Human Action Packet

Status: external-input checklist only.

This packet tells a human operator what must be supplied before the unchecked
Stage 2B ROADMAP parent items can be completed. It is intentionally not a data
collection tool, not a provider connector, not an evaluation runner, and not a
ROADMAP auto-checker.

Machine-readable status is exposed by:

- `build_stage2b_human_action_packet(config=None)`
- `tools/check_stage2b_human_action_packet.py`
- `examples/stage2b_human_action_packet.example.json`

Default status is not ready: the repository does not contain real anonymized
queries, real provider credentials, persistent production indexes, real reranker
evals, or real NLI/LLM semantic-conflict evals.

## Required Human Inputs

| Blocker | ROADMAP line | Human must provide |
| --- | ---: | --- |
| `real_query_set` | 97 | 50-100 anonymized real queries, provenance, anonymization attestation, qrels/relevance targets |
| `real_query_bm25_calibration` | 100 | ready-real query set, corpus snapshot, BM25 sweep manifest with chosen k1/b/threshold and metrics |
| `real_embedding_provider_vector_store` | 103 | embedding provider metadata using `credential_source` env var name only, persistent vector store/index descriptor, production index build manifest |
| `real_reranker_rrf` | 107 | cross-encoder/reranker provider metadata using `credential_source` env var name only, RRF configuration, rerank eval manifest |
| `real_nli_semantic_conflict` | 114 | NLI/LLM provider metadata using `credential_source` env var name only, supports/refutes/not_enough_info labels, semantic-conflict eval manifest and decision policy |

## Honesty Boundary

The packet does not prove that any supplied query is genuinely real, any PII was
removed, any credential exists, any provider is reachable, any model ran, any
index was built, or any metric was achieved. It only names the missing human
actions and mirrors the existing external-dependency audit over declared metadata
shape.
