# Analysis-window token cost (S3.2)

Offline measurement for **U-S3-SG-02**: how many input tokens the extraction windows send
per class hour, how much the overlap costs, and how many windows (provider calls) the plan
forces. These are budget inputs for **G8**, not a provider benchmark.

> **Status: measured locally, provider latency `NO VERIFICADO`.** The harness, the
> `s3-window-cost-v1` fixtures and the numbers below were produced on 2026-10-31 without an
> authorized LLM call. D3-Text is not closed, so no provider latency, output-token price or
> quality result is claimed. A green local run is not a G8 pass.

## Scope

In scope:

- sentence-aligned windows over a persisted transcript, with Unicode code-point offsets;
- a per-window target, a per-window overlap cap and an optional hard total-token budget;
- exact token counts with `cl100k_base` (`tiktoken`), plus the chunker's conservative
  fallback when the encoding is unavailable;
- evidence-identity dedupe (segment + range + literal quote);
- local window-planning latency and the deterministic token projection to a class hour.

Out of scope:

- prompt/system overhead, output tokens, retries, chat history or provider pricing;
- LLM/provider latency and extraction quality (L3.1/L3.7 and a protected `P-*` run);
- real class audio, real class transcripts, student data or any provider call;
- changing the prompt, the analyzer, the `tasks` schema or the web UI.

## Contract under measurement

`apps/api/app/services/analysis_windows.py` plans windows on sentence/segment units shared
with the §M6 chunker. With `target_tokens=800` and `overlap_tokens=100` (the defaults until
the analysis prompt exists):

- each window's measured text is at most `target_tokens`;
- the measured overlap with the previous window is at most `overlap_tokens`;
- the plan advances without gaps, and only the declared overlap is repeated;
- `max_total_tokens`, when set, is a hard measured bound: the planner reduces the overlap
  cap (down to zero) instead of exceeding it, and rejects a budget below the no-overlap
  floor;
- `dedupe_by_evidence` keeps the first item per exact `(segment_id, start, end, quote)`
  identity, so the same task found in two overlapping windows becomes one proposal.

## Fixtures

`apps/api/tests/fixtures/analysis/windows/` contains `s3-window-cost-v1`: three authored,
redacted classroom excerpts in `es`, `en` and `fr` (the F0.1 spike languages) plus a
manifest with their authored spoken duration. The text is not a real class transcript and
contains no student or teacher names; the F0.1 report only retains the synthetic sentence
`El perro corre en el parque todas las mañanas.`, which is too short to measure windows.
No audio, voice, provider output or PII is stored. The class-hour projection is linear from
the authored duration (about 140 words per minute), so it is a budget estimate and not an
observed class.

## Results

Command (from the repository root):

```bash
cd apps/api && uv run python -m tools.eval.window_cost
```

The raw capture is in
[`docs/sprints/artifacts/S3.2/window-cost.txt`](../sprints/artifacts/S3.2/window-cost.txt).
`cl100k_base` exact tokenization was available; local window planning took 7.8–11.1 ms per
seven-minute excerpt (about 3–4 ms per window).

### Default profile: 800 target / 100 overlap

| Lang | Excerpt minutes | Transcript tokens | Windows | Window tokens | Overlap tokens | Amplification | Windows/class-hour | Content tokens/class-hour |
|---|---|---|---|---|---|---|---|---|
| es | 7.33 | 1,584 | 3 | 1,756 | 172 | 1.109 | 24.5 | 14,367 |
| en | 7.00 | 1,185 | 2 | 1,271 | 86 | 1.073 | 17.1 | 10,894 |
| fr | 6.67 | 1,570 | 3 | 1,745 | 175 | 1.111 | 27.0 | 15,705 |

### Profile trade-off

| Profile | Target / overlap | Windows per excerpt (es/en/fr) | Amplification (es/en/fr) | Content tokens/class-hour (es/en/fr) |
|---|---|---|---|---|
| `no-overlap` | 800 / 0 | 3 / 2 / 3 | 1.000 / 1.000 / 1.000 | 12,960 / 10,157 / 14,130 |
| `lean` | 800 / 50 | 3 / 2 / 3 | 1.060 / 1.040 / 1.041 | 13,737 / 10,560 / 14,715 |
| `default` | 800 / 100 | 3 / 2 / 3 | 1.109 / 1.073 / 1.111 | 14,367 / 10,894 / 15,705 |
| `small` | 400 / 50 | 5 / 4 / 5 | 1.110 / 1.104 / 1.094 | 14,392 / 11,211 / 15,462 |

The 100-token overlap costs about 7–11% extra input tokens and does not add provider calls
at this size. Halving the window to 400 tokens raises the calls per class hour by 60–100%
while keeping a similar token bill, so the default stays at 800/100 until the prompts v1 and
a real quality evaluation justify another value.

## G8 budget rows

Add these rows to the G8 budget, with their explicit limits:

| Row | Value | Scope |
|---|---|---|
| Extraction input content tokens per class hour | 10,894–15,705 (default 800/100) | Transcript window text only; add prompt/system overhead and output tokens when L3.1 lands |
| Overlap cost | +7% to +11% over the no-overlap floor | Same fixtures and default profile |
| Extraction calls per class hour | 17–27 (default 800/100) | Windows only; retries, tool calls and chat turns are separate |
| Local window-planning latency | 7.8–11.1 ms per 7-minute excerpt | CPU only, no DB, no provider |
| LLM/provider latency per window | `NO VERIFICADO` | Requires the authorized `P-*` run and D3-Text |

The provider term of the G8 budget is therefore still open:

```
provider time per class hour = extraction calls per class hour x observed latency per call
```

The first factor is 17–27 for the default profile. The second is `NO VERIFICADO` until an
authorized provider run measures it; a mock is not evidence and is not used here.

The linear projection assumes a constant speech rate. A real class includes silence,
questions and non-speech segments, so the observed transcribed tokens per hour may be
lower; the projection is a conservative planning figure, not a measurement of a real class.

## Reproduce

```bash
# Full table, all profiles (exact tokenizer from the API environment)
cd apps/api && uv run python -m tools.eval.window_cost

# JSON for a machine-readable budget
cd apps/api && uv run python -m tools.eval.window_cost --format json

# Same harness through the root shim, with the API environment
cd apps/api && uv run python ../../scripts/eval/window_cost.py --format table

# Tests: planner, dedupe and harness (U-S3-SG-02)
cd apps/api && uv run pytest tests/unit/test_analysis_windows.py tests/unit/test_window_cost.py -q
```

The harness never reads provider credentials, the database or the network. It writes to
stdout only. Raw fixtures are authored text; no generated artifact contains a real class,
audio or PII.
