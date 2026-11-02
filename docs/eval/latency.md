# L2.8 latency protocol

Offline harness for the measured latency objectives of `ESPECIFICACION.md` §8:

1. **Hybrid retrieval p95 ≤ 500 ms** on the frozen reference corpus, per enabled language.
2. **Chat first-token p95 ≤ 5 s** on the same corpus, per enabled language.

The harness calls `SearchService.retrieve` and `RAGService.prepare`/`stream_events` in-process
against a prepared G4 run. It adds no HTTP route, schema, migration, production service or web
change, and it never stores audio.

> **Status: `NOT MEASURED`.** The harness, the protocol and the fake-provider suites are
> delivered on the L2.8 branch. No authorized real embedding/LLM measurement has been
> reviewed and published, so `docs/eval/latency/<run-id>/` does not exist and L2.8 remains
> `in progress`. A green suite or a `SMOKE` report is not an L2.8 pass.

## Scope and non-goals

In scope:

- reuse of the prepared `g4-v1` tenants and index from `tools.eval.cli prepare`;
- retrieval through `SearchService.retrieve(top_k=8)`, one fresh database session per item;
- chat through `RAGService.prepare` plus the SSE stream up to the first non-empty `delta`;
- nearest-rank p50/p95/max per language, per-language gate and an overall verdict;
- a redacted `latency.json` and `REPORT.md` publication with date and environment.

Out of scope:

- HTTP, Caddy, authentication, CSRF, network and provider quota latency;
- load testing, concurrency above 1 or multi-user stress;
- G4 quality metrics, human citation adjudication and the G4 verdict (L2.7);
- real class audio, ingestion, ASR/Riva or any durable audio artifact;
- provider tuning against the `test` partition and new cloud spend.

## Prerequisites

- A reachable PostgreSQL with pgvector, migrated to the current Alembic head.
- Authorized **real** embedding and LLM providers configured through the environment:
  `MAULWURF_EMBEDDING_BASE_URL`, `MAULWURF_EMBEDDING_API_KEY`,
  `MAULWURF_EMBEDDING_MODEL`, `MAULWURF_EMBEDDING_DIMENSION`,
  `MAULWURF_EMBEDDING_ASYMMETRIC` (when the model distinguishes query/passage),
  `MAULWURF_LLM_BASE_URL`, `MAULWURF_LLM_API_KEY`, `MAULWURF_LLM_MODEL` and
  `MAULWURF_LLM_MODEL_WINDOW`.
- The frozen dataset at `apps/api/tests/fixtures/g4/datasets/v1` (`g4-v1`, test partition
  50/35/15 per language).
- A prepared run under `var/eval/g4/<run-id>/` created with the same real providers.
- A clean git worktree at the commit that will be measured. An official run with uncommitted
  changes exits `2`; a smoke run may still record `git_dirty`.

The official command rejects a prepared run that used fake providers, a dataset that is not
exactly 50/35/15, a development-partition run, an inactive index generation and a dirty
worktree.

## Commands

From the repository root:

```bash
# 1. Prepare the isolated G4 tenants and index with the authorized real providers.
cd apps/api
uv run python -m tools.eval.cli prepare \
  --dataset tests/fixtures/g4/datasets/v1 \
  --languages es,en,fr --reset

# 2. Measure retrieval and first-token latency and publish the redacted report.
cd ../..
python scripts/load/latency_rag.py \
  --run-id <run-id> \
  --languages es,en,fr \
  --publish docs/eval/latency/<run-id>
```

The script also accepts `--run-dir`, `--dataset`, `--retrieval-threshold-ms` (default
`500`), `--first-token-threshold-ms` (default `5000`) and `--max-provider-calls` (default
`600`). The provider-call budget includes the calls already spent by `prepare`; the harness
fails before exceeding it, performs no retries and never resumes a partial measurement.

## Method

### Retrieval samples

For every `test` item of each enabled language:

```text
start = perf_counter()
async with session_factory() as db:
    hits = await SearchService(db, provider=embedding).retrieve(
        user_id=index.user_id, query=item.question, top_k=8
    )
took_ms = (perf_counter() - start) * 1000
```

The timer includes session checkout, query embedding, the lexical branch, the vector branch,
RRF fusion, dedupe/diversity and hydration. It excludes the evidence-link audit and every
HTTP layer. An empty result on a populated reference corpus is a measurement failure, not a
fast sample.

### Chat first-token samples

For every `test` item:

1. Create a fresh conversation in a short session. Conversation creation is **not timed**.
2. Start the timer immediately before `RAGService.prepare`.
3. Consume `stream_events` and stop at the first non-empty `delta`.
4. Close the stream explicitly; the isolated conversation keeps a cancelled partial.
5. `source` is `abstention` when `prepared.abstention_text` is set, otherwise `llm`.
6. If closing the stream fails **after** a non-empty delta, the measured sample is kept and
   the report records a limitation. A close failure before the first delta still leaves the
   measurement incomplete.

The timer includes the query embedding, retrieval, prompt assembly, turn persistence and the
provider's first delta. It excludes conversation creation, HTTP, Caddy and network latency.
A `prepare` error, a stream `error`, or a stream that ends without a non-empty delta makes the
measurement incomplete: the harness exits `4` and publishes nothing.

### Cold start and p95

One development item per language is used for one retrieval warm-up and one chat warm-up.
Those values are reported as `cold_retrieval_ms` and `cold_first_token_ms` and are excluded
from the p95 distributions. If the dataset has no development item, the first test item is
used as the cold start **and remains in the distribution**; the report states this limitation.

The p95 is the nearest-rank percentile already used by G4: sort the samples and take
`ceil(0.95 * N)` (1-based). For N=50 it is the 48th smallest sample. There is no interpolation
and no rounding before the comparison.

A language passes only when it produced every expected sample and:

```text
retrieval_p95(L) <= 500.0 ms
first_token_p95(L) <= 5000.0 ms
```

The overall gate passes only when **every enabled language** passes. A macro average never
compensates a failed language. The report also publishes `first_token_llm_only_p95_ms` as a
diagnostic; it never replaces the user-facing first-token gate.

## Artifacts

Published artifacts live under `docs/eval/latency/<run-id>/` and contain aggregates only:

| File | Content |
|---|---|
| `latency.json` | gate, status, date, git commit, environment, dataset version/hash, providers, per-language N/p50/p95/max, cold start, verdict reasons, limitations |
| `REPORT.md` | the same aggregates rendered for review, plus the method, limitations and reproduction command |

The published environment includes Python and platform, CPU count, total host memory,
PostgreSQL and pgvector versions, and an explicit note that Redis is not used by this
in-process path. It never includes a DSN, host name, tenant id or provider credential.

Publication is atomic: both files are written to a sibling temporary directory and the
complete directory is renamed into place. A failed run leaves no partial `latency.json` or
`REPORT.md` and preserves any previous report at that path.

Question, answer, quote and transcript text are never copied into the published directory.
Raw per-item timings, if kept, stay under the gitignored `var/eval/latency/<run-id>/`.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Official measurement complete and every enabled language passes both p95 targets; `PASS` published |
| `1` | Official measurement complete but at least one language fails; the real `FAIL` report is published |
| `2` | Configuration, dataset/hash, provider compatibility or budget error; nothing is published |
| `4` | Not evaluated: fake/smoke run, non-official dataset, development partition, missing sample or inactive generation; nothing is published |
| `3` | Unused by L2.8 (no human adjudication in this gate) |

A `SMOKE` run is available only through the test API (`allow_smoke=True`): it marks the
payload `SMOKE` and always returns `4`. The CLI never enables it, so fake-provider output can
never be published as L2.8 evidence.

## Tests

All tests are mapped to **G4**; L2.8 does not invent a test ID. Prerequisite coverage is
`I-S2-JL-06` (retrieval path) and `I-S2-JL-07/08` (chat stream).

```bash
cd apps/api
uv run pytest tests/unit/test_g4_latency.py -q
uv run pytest -m integration tests/integration/test_g4_latency.py -q
uv run ruff check . && uv run mypy .
```

The unit suite proves nearest-rank statistics, exact thresholds, duplicate/out-of-corpus
sample rejection, consistent thresholds, dynamic N limitations and atomic publication. The
integration suite uses real PostgreSQL/pgvector, the `smoke-v1` corpus, the real service paths
and deterministic fake providers. It proves sample counts, warm-up reporting,
inactive-generation and empty-retrieval rejection, official fake-provider/dataset/dirty-tree
rejection, budget exhaustion, streams without a delta or with an error, close-failure
robustness, redaction and the published metadata. It does not prove provider latency,
provider retention or a real L2.8 result.

## Known limitations

- In-process measurement excludes HTTP, Caddy, authentication, network and provider quota.
- Concurrency is 1: this is a reference measurement, not a load test.
- Cold start is one warm-up item per language, not a process-restart benchmark. When no
  development item exists, the first test item is used and remains in the p95 distribution.
- The p95 uses the frozen test partition; the report states the actual N per language. The
  result is measured, not an SLA or a provider guarantee.
- Provider nondeterminism can change a later run.
- The corpus is synthetic text only; no real classes, audio or PII are involved.
- Stopping the chat stream at the first delta persists an isolated cancelled partial.

## What would close L2.8

1. Authorized real embedding and LLM providers in a protected environment.
2. A `g4-v1` `prepare` and an official latency measurement with every enabled language
   passing both p95 targets.
3. A redacted `latency.json`/`REPORT.md` published under `docs/eval/latency/<run-id>/`.
4. Evidence recorded in `docs/sprints/andres.md`, `docs/sprints/CLAIMS.md` updated to `done`
   and the cloud-eligibility column set to `verified` with date and reviewer.

Until then the harness is delivered, L2.8 stays `in progress` and the latency gate is
**`NOT MEASURED`**.
