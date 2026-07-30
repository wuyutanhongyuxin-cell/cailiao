# Stage 2B Vector Provider Rollout Protocol

This protocol defines the metadata packet required before connecting a real
embedding provider, persistent vector store, and production vector index.

Boundary: this repository still ships deterministic local vector scaffolding only.
The protocol does not call providers, open vector databases, build indexes, read
`.env`, or verify real metrics. It only validates declared rollout metadata.

## Required Packet

- `vector_config`: real provider declaration, env-var credential source name,
  persistent store such as `postgres_pgvector` or `qdrant`, and index metric/dim.
- `index_manifest`: corpus version, chunker version, embedding model/dim,
  distance metric, collection name, build/rebuild command, rollback plan.
- `migration`: preflight checklist, cutover steps, rollback steps.
- `observability`: latency p50/p95, error rate, recall@k, dashboard hooks.
- `acceptance`: rollout gates and rollback trigger.

Credential values are forbidden. Store only environment variable names such as
`OPENAI_API_KEY`; never store keys, tokens, passwords, endpoint secrets, salts, or
`.env` contents.

## Rollout Sequence

1. Select provider and store, document data-retention and regional constraints.
2. Build index in a shadow collection with a manifest.
3. Validate embedding dimension and distance metric consistency across provider,
   index config, and manifest.
4. Run real-query retrieval evaluation outside this packet once a `ready_real`
   query set exists.
5. Canary rollout with latency/error/recall dashboards.
6. Roll back by disabling vector channel and restoring previous collection alias.

## Does Not Prove

- Provider credentials work.
- Vector DB is reachable.
- Index was built.
- Metrics are true.
- ROADMAP line 103 is complete.

Use:

```powershell
python tools\check_stage2b_vector_rollout_protocol.py --json
python tools\check_stage2b_vector_rollout_protocol.py --config examples\stage2b_vector_rollout.example.json --json
```

Default and example packets exit non-zero because no real rollout packet is
present and the example is marked `is_template`.
