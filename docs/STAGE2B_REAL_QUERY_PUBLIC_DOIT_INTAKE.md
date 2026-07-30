# Stage 2B — Public Real-Query Intake from DoIT (candidate evidence)

A deterministic, stdlib-only path to build a **real-query candidate set** for ROADMAP
Stage 2B line 97 from a **licensed public dataset**, instead of synthetic placeholders.

Tool: `tools/prepare_stage2b_real_query_set.py`.

**Boundary (read first):** this produces **candidate evidence** from a public, MIT-licensed
open dataset — user prompts only, no assistant answers. It is **not** private production
telemetry, and it is **not** a synthetic placeholder. Obtaining this candidate set does
**not** by itself complete Stage 2B line 97: the real set still needs review (relevance
targets / qrels, anonymization confirmation, and readiness sign-off) before the parent
blocker can be considered. This task leaves **all five Stage 2B parent blockers unchecked.**

## Public source

- Dataset: **`ChiyuSONG/dynamics-of-instruction-tuning` (DoIT)**
- URL: https://huggingface.co/datasets/ChiyuSONG/dynamics-of-instruction-tuning
- License (per the dataset card): **MIT**
- The card states DoIT has 40k+ human-curated Chinese instruction-output pairs and a
  `Creative Writing` category described as 1,200 "User Queries from In-House Data".
- Record shape: each record has `messages` (user content in `messages[0].content`), plus
  `idx`, `type`, and `question_format`. Creative Writing records have `type == "Creative Writing"`.

Why this is real-query **candidate** evidence and not synthetic: the prompts are
human-authored Chinese user queries from a published, licensed dataset — real language from
real people — as opposed to templated/placeholder text the repo generates for tests.

## How to download the public data (run yourself; the tool never downloads)

The tool operates only on **local** files you have already downloaded. Example ways to fetch
the public DoIT data (outside this repo, on a machine with network access):

```bash
# Option A: huggingface-hub CLI (install separately)
huggingface-cli download ChiyuSONG/dynamics-of-instruction-tuning --repo-type dataset --local-dir ./doit_raw

# Option B: git-lfs clone
git lfs install
git clone https://huggingface.co/datasets/ChiyuSONG/dynamics-of-instruction-tuning ./doit_raw
```

Then point the tool at the downloaded JSON/JSONL:

```bash
# Build a 50-100 case candidate artifact from Creative Writing user prompts
python tools/prepare_stage2b_real_query_set.py prepare \
  --input ./doit_raw --output ./doit_real_query_candidate.json --min 50 --max 100

# Validate an existing artifact (schema / count / license / source / content)
python tools/prepare_stage2b_real_query_set.py validate \
  --artifact ./doit_real_query_candidate.json
```

`prepare` exits nonzero (and writes no artifact) if it cannot select `--min` valid prompts —
it never fabricates records. `validate` exits nonzero if any schema/count/license/source/
content check fails.

## What the artifact contains

- `source` (name, URL, MIT license, `Creative Writing` category), `extraction_method`, `record_count`.
- `cases[]`: each with `id`, `query` (the user prompt), `query_sha256`, `source_type`,
  `question_format`, `source_idx`. **No assistant answers.**
- `set_hash` (deterministic sha256 over the ordered per-prompt hashes), `selection_stats`,
  `contains_assistant_answers: false`, `roadmap_parent_items_checked: false`.

## Selection / filtering rules (deterministic)

- Only `type == "Creative Writing"` records.
- Reject prompts shorter than 6 chars, empty, or with fewer than 2 CJK characters (non-Chinese).
- De-duplicate on normalized prompt text; preserve first-seen order; cap at `--max`.

## Network status in this environment

At implementation time the isolated WSL session had **no outbound network / DNS** (see the
task report), so **no real DoIT artifact was produced here**. Only the tooling, docs, and
tests (with an invented fixture) were added. Run the download commands above on a networked
machine to generate a real artifact.

## Fixture

`tests/data/doit_fixture.sample.jsonl` is an **invented fixture** (its first record is marked
`FIXTURE ONLY — invented records for tests, not real DoIT evidence`). It exercises the tool's
selection/validation logic in tests. It is not real DoIT data and must not be mistaken for
real evidence.
