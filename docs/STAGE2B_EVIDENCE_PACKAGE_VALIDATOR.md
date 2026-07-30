# Stage 2B Evidence Package Validator

Aggregates all Stage 2B external-blocker evidence into one machine-readable readiness
report. Produced by `build_stage2b_evidence_package_validator(config=None)`, which reuses
the existing audit and the seven Stage 2B contract/status helpers as the single source of
truth for each evidence group's current state.

**Boundary (read first):** this is a **metadata / package validator only**. It declares what
evidence each blocker must produce and reports each group's current readiness by delegating
to the existing helpers. It makes no network/provider call, downloads no model, runs no eval,
reads no artifact file or hash, reads no credential/`.env`, and fabricates no approval or risk
acceptance. `ready_for_stage2b_completion` is false by default, and **no ROADMAP parent item
is auto-checked** — all five parents (lines 97/100/103/107/114) stay unchecked until real
data/providers are integrated and evaluated.

## Required evidence groups (fixed order)

1. `declared_artifacts` — the declared artifact packet for the blocker (`build_stage2b_artifact_contracts`).
2. `eval_run_manifest` — a real eval-run manifest (`build_stage2b_eval_run_contract`).
3. `observability_snapshot` — telemetry contract satisfied (`build_stage2b_observability_contract`).
4. `release_dossier` — release dossier / model-data cards (`build_stage2b_release_dossier`).
5. `reproducibility_provenance` — PROV-style provenance + immutable ids (`build_stage2b_reproducibility_provenance`).
6. `risk_treatment` — risk register entries closed (`build_stage2b_risk_register`).
7. `industry_checklist` — industry implementation checklist complete (`build_stage2b_industry_implementation_checklist`).

## Blockers (fixed order)

`real_query_set` (line 97), `real_query_bm25_calibration` (line 100),
`real_embedding_provider_vector_store` (line 103), `real_reranker_rrf` (line 107),
`real_nli_semantic_conflict` (line 114).

For each blocker the validator lists, per evidence group, the required item that would close
that group (see `evidence_requirements` in the output). Example: for `real_query_set`,
`eval_run_manifest` requires "an eval-run manifest whose `dataset_readiness_status` is
`ready_real`".

## Readiness logic

- Each evidence group is `ready` only if its underlying helper reports readiness
  (declared-metadata shape only): e.g. `eval_run_manifest` ready when
  `build_stage2b_eval_run_contract().has_real_eval_run` is true; `risk_treatment` ready when
  `build_stage2b_risk_register().all_risks_closed` is true; etc.
- `ready_for_stage2b_completion` is true only when **every** evidence group is ready AND the
  external-dependency audit reports **every** blocker satisfied. False by default.

## Usage

```bash
python tools/check_stage2b_evidence_package_validator.py --json
# default repo state: ready_for_stage2b_completion=false, all 7 groups MISSING, exit 1
```

`examples/stage2b_evidence_package.example.json` is a template-only snapshot of the default
(not-ready) state; it carries no real data, secrets, or fabricated approval.

## Boundary restated
- Default not ready; all five ROADMAP parents unchecked.
- Group readiness is declared-metadata shape only, not real provider connectivity/eval.
- No network, no provider call, no model download, no eval, no artifact/hash file read, no
  credential/`.env` read, no approval/risk-acceptance fabrication, no ROADMAP parent auto-check.

## References
- NIST AI RMF / AIRC (governance, documentation, evidence, TEVV): https://www.nist.gov/itl/ai-risk-management-framework
- BEIR retrieval evaluation: https://github.com/beir-cellar/beir
- Google Model Cards: https://modelcards.withgoogle.com/about
- OpenTelemetry (observability evidence): https://opentelemetry.io/docs/
- FEVER fact verification: https://fever.ai/
