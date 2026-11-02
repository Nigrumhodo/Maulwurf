# Final G4 and G6 metrics (L4.1)

Published numerators and denominators per language. A replay that meets a numeric
threshold is not an approval. Anything that was not measured, or that the replay got
wrong, is listed below and is not dressed up as a pass.

> **Neither gate is approved.** G4 is `NOT EVALUATED`. The G6 real-provider measurement
> is `NO VERIFICADO`.

## G4

Status: `NOT EVALUATED`. There is no numerator and no denominator. No authorized
embedding or LLM run has been reviewed, and no
`docs/eval/g4/<run-id>/REPORT.md` exists. The protocol is
[docs/eval/README.md](README.md).

| Language | Partition | Recall@8 | Citation precision | Abstention | Unauthorized |
|---|---|---|---|---|---|
| es | test | no numerator | no numerator | no numerator | no numerator |
| en | test | no numerator | no numerator | no numerator | no numerator |
| fr | test | no numerator | no numerator | no numerator | no numerator |

Hard slice: the whole official `test` partition (`g4-v1`, 35 answerable and 15
unanswerable items per language). It failed to be scored because the authorized run and
the human adjudication never happened. A green CI suite with fake providers is not this
number.

Thresholds, unmet because they were not measured: Recall@8 ≥ 0.85, citation precision
≥ 0.95, abstention ≥ 0.90, zero unauthorized citations.

## G6 replay

Harness: [`scripts/eval/g6.py`](../../scripts/eval/g6.py) (`apps/api/tools/eval/g6.py`)
on frozen `g6-v1`. This is an authored stand-in, not a provider capture and not
P-S3-JL-07. The protocol and the same ratios are in [g6.md](g6.md).

| Language | Partition | Gold | Precision | Recall | Exact date | Rejected |
|---|---|---|---|---|---|---|
| es | test | 50 | 47/48 | 47/50 | 44/45 | 1 |
| en | test | 50 | 47/48 | 47/50 | 44/45 | 1 |

The replay ratios meet the numeric thresholds (precision ≥ 95/100, recall ≥ 85/100,
exact date ≥ 95/100). That does not close G6. The real provider measurement stays
`NO VERIFICADO`.

Hard slices, the same in `es` and `en`:

- Activities 0 and 1 are omitted from the recording, so recall misses them.
- Activity 2 is published with `day_offset` seven days late, so exact date misses it.
- One invented quote is rejected by `validate_analysis` and is not published.

Ambiguous gold items stay unresolved (`date_status=ambiguous`, both dates null). DST is
resolved by `resolve_due`, not by the model's `due_at`.

## Backlog

These stay open. They are not L4.6's backlog, which that ticket still has to write.

- Authorized G4 run on the `g4-v1` `test` partition, with human adjudication and a
  redacted report. Until then G4 stays `NOT EVALUATED`.
- P-S3-JL-07, the authorized G6 measurement. Until then the real number stays
  `NO VERIFICADO` and G6 is not approved.
