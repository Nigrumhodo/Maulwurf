# L2.8 latency report — g4-v1-f4a4cf47-20261102T074010Z

**Gate status:** **PASS**

| Field | Value |
|---|---|
| Measured at | `2026-11-02T07:44:18+00:00` |
| Dataset | `g4-v1` (`sha256:f4a4cf474ae26863679d6b69c8fa8266385907ca12c3dd10371d1a69c1934677`) |
| Partition | `test` |
| Git commit | `2dbf60049b0fdc567b86e227d02fb705ec351583` |
| Git dirty | `False` |
| Concurrency | 1 |
| Embedding | `HttpEmbeddingProvider` / `nvidia/nemotron-3.5-embed-1b` (dimension 1536) |
| LLM | `HttpChatProvider` / `nvidia/nemotron-3.5-bolt-e4b` |
| Prompt version | `v1` |
| Environment | `3.12.3` / `Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39` |

## Verdict

| Language | Retrieval N | Retrieval p50/p95/max (ms) | Retrieval pass | First-token N | First-token p50/p95/max (ms) | First-token pass | LLM-only p95 (ms) |
|---|---|---|---|---|---|---|---|
| es | 50 | 329.73 / 367.44 / 559.40 | yes | 50 | 1124.55 / 1191.61 / 1839.59 | yes | 1191.61 |
| en | 50 | 329.20 / 365.37 / 388.79 | yes | 50 | 1212.70 / 1581.03 / 3395.31 | yes | 1581.03 |
| fr | 50 | 328.44 / 402.22 / 1001.22 | yes | 50 | 1126.94 / 1177.48 / 1233.28 | yes | 1177.48 |

## Cold start (excluded from p95)

| Language | Retrieval (ms) | First token (ms) |
|---|---|---|
| es | 394.71 | 1350.62 |
| en | 325.77 | 1640.37 |
| fr | 326.47 | 1168.47 |

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
  --run-id g4-v1-f4a4cf47-20261102T074010Z --languages es,en,fr \
  --publish docs/eval/latency/g4-v1-f4a4cf47-20261102T074010Z
```
