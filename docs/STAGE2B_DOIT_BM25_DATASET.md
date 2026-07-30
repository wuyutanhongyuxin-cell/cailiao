# Stage 2B — DoIT prompt-answer BM25 calibration dataset

A deterministic, stdlib-only converter that turns **locally-downloaded** DoIT
prompt-answer records into a `ready_real` query set with a **paired corpus**, ready for the
existing gated BM25 sweep (`server.run_bm25_sweep_on_real_query_set`).

Tool: `tools/build_stage2b_doit_bm25_dataset.py` (`build` / `validate`).

## Boundary (read first)

This dataset is a **real public benchmark seed**, NOT private production telemetry, and
**NOT final production calibration by itself**:
- The prompts and answers come from the public, **MIT-licensed** Hugging Face dataset
  `ChiyuSONG/dynamics-of-instruction-tuning` (DoIT). They are open benchmark data, not user
  telemetry.
- Using DoIT assistant answers as retrieval documents is a **seed corpus** for calibrating
  BM25 parameters; it is not the repo's real production corpus, and a good sweep result on
  it does not mean production retrieval is validated.
- Building this dataset does **not** complete any Stage 2B ROADMAP parent blocker. It gives
  the gated sweep something real to run on; final calibration/acceptance is a separate,
  human-reviewed step. **All five parent blockers remain unchecked** in this task.

## Source

- Dataset: `ChiyuSONG/dynamics-of-instruction-tuning` (DoIT), license **MIT**.
- URL: https://huggingface.co/datasets/ChiyuSONG/dynamics-of-instruction-tuning
- Real file used by Codex: `curated/1000/creative_writing_1000.json`
  (fetched from `.../resolve/main/curated/1000/creative_writing_1000.json`, SHA256
  `9eed74db9e9fc758104739fa5f5133499606a50485ba11aa6caa01cf5adcec92`).
- Real records use `type == "creative_writing"` (snake_case) and hold a prompt in
  `messages[0]` (user) and an answer in `messages[1]` (assistant). The tool accepts both
  the snake_case label and the card display label `Creative Writing` (same aliasing as the
  intake tool).

## Usage (download yourself; the tool never downloads)

```bash
# Fetch the public MIT file on a networked machine (outside this repo), e.g.:
huggingface-cli download ChiyuSONG/dynamics-of-instruction-tuning \
  --repo-type dataset --include 'curated/1000/creative_writing_1000.json' --local-dir ./doit_raw

# Build a ready_real query set + paired corpus:
python tools/build_stage2b_doit_bm25_dataset.py build \
  --input ./doit_raw/curated/1000/creative_writing_1000.json \
  --output ./doit_bm25_dataset.json \
  --source-file curated/1000/creative_writing_1000.json --min 50 --max 100

# Validate it (delegates readiness to server.summarize_real_query_readiness):
python tools/build_stage2b_doit_bm25_dataset.py validate --set ./doit_bm25_dataset.json

# Then run the existing gated BM25 sweep on it (server helper):
#   server.run_bm25_sweep_on_real_query_set(json.load(open('./doit_bm25_dataset.json')))
```

`build` exits nonzero and writes nothing if it cannot select `--min` valid records — it
never fabricates rows.

## What the dataset contains

- `metadata`: `name`/`version`/`description` (kept free of placeholder tokens so readiness
  is not misclassified as a template), `source_url`, `source_file`, `license` (MIT),
  `category`, `extraction_method`, `anonymized: true`, `is_template: false`, and a boundary note.
- `cases[]`: `id`, `query` (user prompt), `query_sha256`, `provenance {source, collected_at, anonymized: true}`,
  `relevant_titles` (points at the paired corpus doc), `answer_sha256`, `source_type`
  (canonical label), `source_type_raw` (record's own label), `source_idx`.
  **No assistant answer text is stored on a case.**
- `corpus[]`: one document per case — `id`, `title` (matches the case's `relevant_titles`),
  `text` (the public DoIT assistant answer), `format`, `status`.
- `set_hash` / `corpus_hash` for tamper detection; `selection_stats`;
  `contains_assistant_answers_in_cases: false`; `roadmap_parent_items_checked: false`.

## Selection rules (deterministic)

- Only Creative Writing records (both `creative_writing` and `Creative Writing` accepted).
- Prompt must be a valid Chinese prompt (≥6 chars, ≥2 CJK chars) with no PII-shaped value.
- The record must have a non-empty assistant answer (≥8 chars) to serve as its corpus doc.
- De-duplicate on prompt text; preserve first-seen order; cap at `--max`.

## Network status in this environment

The isolated WSL session has no outbound network, so this task did not download the real
DoIT file or produce a real dataset here — only the tool, docs, tests, and an invented
fixture were added. Run the `huggingface-cli` command above on a networked machine to build
a real dataset. Codex will decide on running the sweep against the real downloaded file.

## Fixture

`tests/data/doit_bm25_fixture.sample.jsonl` is an **invented** fixture (first record marked
`FIXTURE ONLY — invented prompt-answer records for tests, not real DoIT evidence`). It mixes
both category labels and includes noise (no-answer, non-Chinese, duplicate, other category)
to exercise selection/validation. It is not real DoIT data.
