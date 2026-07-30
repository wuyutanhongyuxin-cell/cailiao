# Stage 2B Reproducibility Provenance

Status: provenance/reproducibility checklist only.

This checklist names the entities, activities, agents, artifacts, and immutable
identifiers required to reproduce each Stage 2B external evidence package. It
aligns with W3C PROV concepts, TREC-style qrels/run/eval conventions, ML
reproducibility checklists, and NIST trustworthiness guidance. It does not verify
hashes against contents, run evals, call providers, or mark ROADMAP parents
complete.

Machine-readable status:

- `build_stage2b_reproducibility_provenance(config=None)`
- `tools/check_stage2b_reproducibility_provenance.py`
- `examples/stage2b_reproducibility_provenance.example.json`

## Checklist

| Blocker | ROADMAP line | Provenance focus | Required immutable identifiers |
| --- | ---: | --- | --- |
| `real_query_set` | 97 | query set, qrels, anonymization policy, corpus snapshot | dataset id, case ids, qrels hash, source snapshot id |
| `real_query_bm25_calibration` | 100 | query set, corpus, sweep grid, sweep results | run id, dataset id, corpus id, grid values, metric hash |
| `real_embedding_provider_vector_store` | 103 | provider card, vector store config, index manifest, eval run | model id, embedding dim, index id, metric, dataset id |
| `real_reranker_rrf` | 107 | reranker card, RRF config, candidate runfile, rerank eval | model id, rank constant, rank window, top-k, run id |
| `real_nli_semantic_conflict` | 114 | NLI card, label set, semantic policy, review log | policy id, model id, label set id, evidence snapshot id, run id |

## Boundary

Provenance checklist readiness is not reproducibility proof. Real artifacts,
hashes, snapshots, and run manifests must exist before parent items can be
considered complete.
