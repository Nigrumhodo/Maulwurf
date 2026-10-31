# G4 evaluation protocol

Offline harness for gate **G4**: Recall@8 on answerable questions, human-reviewed citation
precision, human-reviewed abstention and zero unauthorized citations. The harness calls
`SearchService` and `RAGService` in-process. It adds no HTTP route, request/response schema,
migration or production service behavior.

> **Status: `NOT EVALUATED`.** The harness, the frozen `g4-v1` dataset and the fake-provider
> suites are delivered on the L2.7 branch, but no authorized real embedding/LLM run has been
> reviewed and published. A green CI suite is not a G4 pass, and no
> `docs/eval/g4/<run-id>/REPORT.md` exists yet. G4 closes only with the real run and human
> adjudication described below.

## Scope and non-goals

In scope:

- frozen synthetic dataset `g4-v1` for `es`, `en` and `fr`;
- tenant-isolated seeding and text indexing of the synthetic corpus;
- retrieval at `top_k=8` with evidence matching over **all** `chunk_segments` links;
- RAG answers through the real service pipeline with citation auditing;
- pure metric functions with exact integer threshold comparisons;
- human review sheets, redacted publication and stable CLI exit codes.

Out of scope:

- real-class ingestion, audio, storage, playback or retranscription;
- web UI, conversation CRUD, new routes or SSE router changes;
- a dedicated French FTS configuration (v1 uses `simple`);
- localizing `ABSTENTION_TEXT`;
- tuning prompts or models against the `test` partition;
- CI jobs with provider secrets, scheduled cloud spend or automatic G4 verdicts.

## Dataset

`apps/api/tests/fixtures/g4/datasets/v1/` contains 10 authored synthetic classes per
language (14 segments and roughly 1,050–1,175 words each) and 55 items per language:

- `test`: 50 items, 35 answerable and 15 unanswerable — the only partition that can emit a
  G4 verdict;
- `development`: 5 items (4 answerable, 1 unanswerable) for harness and prompt work only.

The dataset README documents provenance, authoring rules, evidence offsets and the human
review protocol. `g4-v1` is immutable: the loader rejects any file or aggregate hash change
before a database or provider call. A correction requires a new version directory.

`apps/api/tests/fixtures/g4/datasets/smoke-v1/` is a tiny CI dataset (2 classes and 4 test
items per language). It is not official size and must never produce a G4 pass. Integration
tests score it with `score_run(require_official=False)`; the CLI always enforces the official
partition.

## Commands

All commands run from `apps/api`:

```bash
# 1. Dataset-only validation: no database, no provider.
uv run python -m tools.eval.cli validate \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr
uv run python -m tools.eval.cli validate \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr --official

# 2. Seed three isolated eval tenants and build/activate their index (real embeddings).
uv run python -m tools.eval.cli prepare \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr --reset

# 3. Retrieve and answer the frozen test partition (real embeddings + LLM).
uv run python -m tools.eval.cli run \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr \
  --partition test --run-id 2026-10-30T12-00Z-g4-v1

# 4. A human reviewer fills review.jsonl (review.csv is the spreadsheet copy).
# 5. Validate adjudication, enforce thresholds and publish the redacted report.
uv run python -m tools.eval.cli score \
  --run-id 2026-10-30T12-00Z-g4-v1 \
  --adjudication var/eval/g4/2026-10-30T12-00Z-g4-v1/review.jsonl \
  --publish docs/eval/g4/2026-10-30T12-00Z-g4-v1
```

`scripts/eval/g4.py` is a thin shim for the same CLI when running from the repository root.

The CLI never falls back to fake providers. If no real provider is configured, `prepare` and
`run` exit `4` (`NOT EVALUATED`). Fakes exist only in tests and are deterministic.

`prepare` derives one tenant per language:

```
user_id    = uuid5(NAMESPACE_URL, "maulwurf:g4:<dataset_version>:<language>")
google_sub = "g4-eval:<dataset_version>:<language>"
email      = "g4-eval+<language>@example.invalid"
```

The reset path is scoped by that user ID and verified against the evaluation marker before it
deletes anything. It never deletes by email or display name. No audio file, bytes, hash or
storage key is created. The seeder writes subjects, audios, transcript v1, segments, chunks,
embeddings and one active index generation per language, then verifies that every expected
`cita_textual` appears in at least one chunk of its class.

`run` uses one fresh conversation per item, consumes the real SSE stream, and independently
checks every emitted citation against the tenant, active generation, transcript/audio,
segment link and the exact sources the model was shown. Any failure counts as unauthorized
and is a hard gate failure. An errored or missing answer leaves the run incomplete; `score`
then exits `4` and never scores a partial partition.

## Artifacts

Raw artifacts live under `var/eval/g4/<run-id>/` and are gitignored because they contain
question, answer and quote text:

| File | Content |
|---|---|
| `run.json` | run ID, timestamps, git commit, dataset version/hash, partition, languages, tenant/generation IDs, provider models and dimension, prompt version, budget |
| `corpus-map.json` | `class_ref` → audio ID, tenant IDs, generation versions, chunk counts |
| `retrieval.jsonl` | per-item ranked chunk IDs, expected/matched spans and timings |
| `answers.jsonl` | per-item answer, citations, status, source count and timings |
| `review.jsonl` / `review.csv` | one human-review row per citation and per unanswerable item |
| `published/` | redacted report generated by `score` |

Published artifacts under `docs/eval/g4/<run-id>/` are redacted and safe to commit:

- `REPORT.md` — formulas, per-language numerator/denominator, thresholds, dataset version
  and hash, model/prompt versions, reviewer/date and limitations;
- `metrics.json` — the same aggregates in machine-readable form, including Wilson 95%
  intervals as diagnostics;
- `adjudication.redacted.jsonl` — `row_id`, kind and verdict only, never question, answer or
  quote text.

Resume uses `--resume`: completed items are skipped, failed items are retried in a fresh
conversation with a new client message ID, and successful items are never re-answered or
cherry-picked. The provider-call budget defaults to 400 and aborts before exceeding it.

## Metrics

For each enabled language `L`, using the `test` partition only:

```
Recall@8(L) =
  |{q in answerable(L) : top8(q) matches >=1 expected evidence of q}|
  / |answerable(L)|

citation_precision(L) =
  |{c in citations(L) : reviewer_verdict(c) = correct}| / |citations(L)|

abstention(L) =
  |{q in unanswerable(L) : verdict(q) = abstained and citations(q) = empty}|
  / |unanswerable(L)|
```

A chunk matches expected evidence `e` only when all of the following hold:

- the chunk belongs to the audio of `e.class_ref`;
- `chunk.transcript_version == e.transcript_version`;
- a `chunk_segments` link exists to the segment with `e.segment_ordinal`;
- `e.cita_textual` is a literal substring of `chunk.content`.

The link check queries **all** links for the retrieved chunk IDs; it does not trust the single
segment hydrated for display.

A language passes only with:

- Recall@8 ≥ 0.85;
- citation precision ≥ 0.95;
- abstention ≥ 0.90;
- zero unauthorized citation IDs;
- at least one reviewed citation (zero citations fail: precision is undefined, not 1.0).

Overall G4 passes only when **every** enabled language passes. The macro average is reported
for information and never compensates a failed language. Thresholds compare integers
(`hits * 100 >= percent * total`), so 30/35 and 14/15 pass while 29/35 and 13/15 fail.
Wilson 95% intervals, span recall, citation coverage, answerable abstentions and retrieval
p50/p95 are diagnostic only; L2.8 owns the latency gate.

## Human review

`run` emits one review row per distinct citation and one per unanswerable item, with
`verdict`, `reviewer`, `reviewed_at` and `notes` empty. The reviewer:

- marks a citation `correct` only when the quote supports the answer; otherwise
  `incorrect`;
- marks an unanswerable item `abstained` only when the answer refuses **and** has no
  citations; otherwise `answered`;
- signs every row with a stable reviewer ID and an ISO-8601 UTC timestamp.

Missing, extra or unsigned rows make `score` exit `3` and publish nothing. A single reviewer
is accepted for v1 but must be listed as a limitation. If a row is disputed, record the
second review in `notes`; do not edit the model output.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | All enabled languages pass with complete human adjudication |
| `1` | Gate failure: a threshold, an unauthorized citation or zero reviewed citations |
| `2` | Configuration, dataset, hash, provider-configuration or preparation error |
| `3` | Incomplete or invalid human adjudication; nothing is published |
| `4` | Not evaluated: real providers unavailable, partial/errored run or development partition |

A `1` still publishes a `FAIL` report with the real numbers. A `3` or `4` publishes nothing.

## Tests and CI

```bash
cd apps/api
uv run pytest tests/unit/test_g4_metrics.py tests/unit/test_g4_dataset.py -q
uv run ruff check .
uv run mypy .
# Integration needs a reachable PostgreSQL+pgvector and Redis; see docs/RUNBOOK-dev.md.
MAULWURF_DATABASE_URL=postgresql+asyncpg://maulwurf:maulwurf@127.0.0.1:55432/maulwurf \
  uv run pytest -m integration tests/integration/test_g4_eval.py -q
```

The integration suite uses a deterministic hashed-bag-of-words embedding provider and a
scripted LLM, with the real migrations and real service code. It proves tenant isolation,
retrievability, citation auditing, adjudication completeness, per-language verdicts,
redaction and exit codes. It does not prove provider quality, provider retention, real
abstention behavior or a real G4 result. No fake provider run may be published as G4
evidence.

CI runs the unit tests and the integration suite with service containers. It never has
provider credentials and never produces a G4 verdict.

## Known limitations

- Synthetic text only; results do not generalize automatically to real classes.
- Small samples: N=35 answerable and N=15 unanswerable per language; Wilson intervals are
  reported but the gate uses point estimates.
- A single reviewer is accepted for v1; inter-annotator agreement is not measured.
- Provider nondeterminism can change answers between runs; the dataset hash pins the input,
  not the model output.
- French uses the `simple` FTS configuration in v1.
- `ABSTENTION_TEXT` is Spanish-only; English and French refusals are identified by human
  review, not by a localized string.
- No real class audio, PII or provider retention evidence is involved.

## What would close G4

1. Authorized real embedding and LLM providers in a protected environment.
2. `prepare` + `run` on the exact `test` partition with the `g4-v1` hash.
3. Complete human adjudication by a named reviewer, including the date.
4. `score` with all enabled languages passing, zero unauthorized citations and a published
   redacted report under `docs/eval/g4/<run-id>/`.
5. Evidence recorded in `docs/sprints/andres.md` and `docs/sprints/CLAIMS.md` updated to
   `done` only after review and CI are green.

Until then, the claim stays `in progress` and G4 is **`NOT EVALUATED`**.
