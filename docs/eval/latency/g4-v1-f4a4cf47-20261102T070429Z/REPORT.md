# L2.8 latency report — g4-v1-f4a4cf47-20261102T070429Z

**Gate status:** **PASS**

| Field | Value |
|---|---|
| Measured at | `2026-11-02T07:08:58+00:00` |
| Dataset | `g4-v1` (`sha256:f4a4cf474ae26863679d6b69c8fa8266385907ca12c3dd10371d1a69c1934677`) |
| Partition | `test` |
| Git commit | `a0b7af8e12f71a91d3b3e2b910639f3b4fac5d21` |
| Git dirty | `False` |
| Concurrency | 1 |
| Embedding | `HttpEmbeddingProvider` / `nvidia/nemotron-3.5-embed-1b` (dimension 1536) |
| LLM | `HttpChatProvider` / `nvidia/nemotron-3.5-bolt-e4b` |
| Prompt version | `v1` |
| Environment | `3.12.3` / `Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39` |

## Verdict

| Language | Retrieval N | Retrieval p50/p95/max (ms) | Retrieval pass | First-token N | First-token p50/p95/max (ms) | First-token pass | LLM-only p95 (ms) |
|---|---|---|---|---|---|---|---|
| es | 50 | 329.64 / 363.84 / 371.15 | yes | 50 | 1122.28 / 1229.91 / 1316.20 | yes | 1229.91 |
| en | 50 | 332.77 / 355.00 / 375.60 | yes | 50 | 1207.61 / 2637.13 / 6624.39 | yes | 2637.13 |
| fr | 50 | 332.19 / 372.98 / 403.63 | yes | 50 | 1153.66 / 1225.39 / 2804.60 | yes | 1225.39 |

## Cold start (excluded from p95)

| Language | Retrieval (ms) | First token (ms) |
|---|---|---|
| es | 402.33 | 1251.87 |
| en | 328.54 | 1316.85 |
| fr | 313.74 | 1127.31 |

## Method

- Retrieval p95 times `SearchService.retrieve(top_k=8)` in-process, one fresh database session per item: session checkout, query embedding, both branches, RRF fusion and hydration.
- Chat first-token p95 times `RAGService.prepare` plus the SSE stream up to the first non-empty `delta`. It includes embedding, retrieval, prompt assembly, the turn persistence and the provider's first delta.
- p95 is nearest-rank over the frozen `test` partition; no interpolation and no rounding before the comparison.
- The gate passes only when every enabled language is complete and both p95 values are `<=` their target. The LLM-only p95 is a diagnostic, not the gate.

## Limitations

- In-process measurement: it excludes HTTP, Caddy, authentication, network and provider quota enforcement.
- Concurrency is 1; this is a reference measurement, not a load test.
- Cold start is one warm-up item per language, reported separately; it is not a process-restart benchmark. When no development item exists, the first test item serves as cold start and remains in the p95 distribution.
- Chat first-token timing stops at the first non-empty delta; closing the stream early persists an isolated cancelled partial for that item's conversation.
- The p95 uses nearest-rank over the frozen test partition (N=50 per language); the result is measured, not an SLA or a provider guarantee.
- Synthetic text corpus only: no real classes, audio or personally identifiable data.
- Provider nondeterminism can change a later run.
- Every enabled language must pass on its own; a macro average never compensates a failed language.

## Reproduction

```bash
cd apps/api
uv run python -m tools.eval.cli prepare \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr --reset
cd ../..
python scripts/load/latency_rag.py \
  --run-id g4-v1-f4a4cf47-20261102T070429Z --languages es,en,fr \
  --publish docs/eval/latency/g4-v1-f4a4cf47-20261102T070429Z
```
