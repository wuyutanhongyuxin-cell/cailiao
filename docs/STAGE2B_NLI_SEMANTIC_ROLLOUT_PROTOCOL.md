# Stage 2B/3 NLI Semantic Rollout Protocol

This protocol defines the metadata packet required before enabling real NLI/LLM
semantic entailment and conflict evidence detection.

Boundary: this repository still ships deterministic lexical conflict detection
only. It does not perform semantic entailment (`does_semantic_entailment=false`).
This protocol does not call providers, download models, run evaluations, read
`.env`, or verify real metrics.

## Required Packet

- `semantic_config`: real NLI/LLM provider, model, credential env-var source,
  eval labels, and decision policy.
- `label_mapping`: FEVER-style `supports/refutes/not_enough_info` coverage and
  SNLI/MNLI-style `entailment/contradiction/neutral` mapping.
- `policy`: verdict labels, `min_confidence`, `block_on`, and `warn_on`.
- `evidence_requirements`: claim text, cited chunk IDs, context window, provenance.
- `eval_packet`: per-label precision/recall/F1, confusion matrix, calibration
  notes, abstention rate, refusal rate, and run manifest reference.
- `human_review`: escalation triggers and review queue.
- `observability`: provider error rate, latency p95, verdict distribution,
  escalation rate.
- `rollout`: preflight, canary, rollback steps, rollback trigger.

## Risk Controls

The rollout follows NIST AI RMF style controls: govern ownership and review
queues, map claim/evidence/provenance risks, measure label quality and calibration,
and manage rollout through canary, monitoring, and rollback.

## Does Not Prove

- Provider credentials work.
- NLI/LLM inference ran.
- Semantic labels are correct.
- Eval run happened.
- ROADMAP line 114 is complete.

Use:

```powershell
python tools\check_stage2b_nli_semantic_rollout_protocol.py --json
python tools\check_stage2b_nli_semantic_rollout_protocol.py --config examples\stage2b_nli_semantic_rollout.example.json --json
```

Default and example packets exit non-zero because no real rollout packet is
present and the example is marked `is_template`.
