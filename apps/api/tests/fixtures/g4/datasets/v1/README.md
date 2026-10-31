# G4 dataset `g4-v1`

Frozen evaluation corpus and question set for gate **G4** (Recall@8, citation
precision, abstention). This directory is versioned and **immutable**: once a report cites
`g4-v1`, the `test` partition must not change. Any correction requires a new version
directory and a new `dataset_version` in `manifest.json`; the loader verifies every file
hash and the aggregate `content_sha256` before a database or provider call.

## Provenance and privacy

- Every class transcript was authored for this evaluation. No real class, recording,
  transcript, student, teacher, institution or other PII is present.
- No audio bytes exist. The corpus is JSONL text only; the seeder creates database rows,
  never a file, storage key or audio hash.
- The content is synthetic and deliberately generic. It is not suitable as a factual
  reference, and it must not be presented as a real class or as evidence of provider
  behaviour.
- `created_at` in the manifest is the authoring date. The dataset is reviewed as text; no
  provider was called while producing it.

## Layout

```
v1/
  manifest.json          dataset version, counts, per-file SHA-256, aggregate hash
  README.md              this file
  corpus/{es,en,fr}.jsonl  10 synthetic classes per language
  items/{es,en,fr}.jsonl   55 questions per language (50 test + 5 development)
```

The CI smoke dataset lives in `../smoke-v1/` and is intentionally **not** official size.
It exists to exercise the harness with fake providers and must never be used for a G4
verdict.

## Size and partitions

| Language | Classes | Segments/class | Words/class | Test | Answerable | Unanswerable | Development |
|---|---|---:|---:|---:|---:|---:|---:|
| `es` | 10 | 14 | 1,087–1,112 | 50 | 35 | 15 | 5 |
| `en` | 10 | 14 | 1,058–1,075 | 50 | 35 | 15 | 5 |
| `fr` | 10 | 14 | 1,146–1,174 | 50 | 35 | 15 | 5 |

Each language has 40 expected-evidence spans across its 35 answerable test items (one item
carries two spans). The development partition has four answerable and one unanswerable item
per language; it is for harness and prompt development only and is never scored for G4.

The French corpus uses the PostgreSQL `simple` text-search configuration in v1. A dedicated
`french` FTS arm is out of scope for L2.7 and would require its own ticket and reindex.

## Corpus format

One JSON object per line:

```json
{
  "class_ref": "es-algebra-01",
  "language": "es",
  "subject": "Álgebra lineal",
  "class_date": "2026-09-08",
  "transcript_version": 1,
  "segments": [
    {"ordinal": 1, "text": "..."},
    {"ordinal": 2, "text": "..."}
  ]
}
```

- `class_ref` is stable and unique across the dataset; items use it to anchor evidence.
- `transcript_version` is `1` for every class in v1.
- Ordinals are 1-based, increasing and contiguous.
- `Transcript.text` is the segments joined with `"\n"`. The seeder derives global
  `char_start`/`char_end` from that exact string (storing the separator on the preceding
  segment), so text and offsets stay consistent.
- `timestamp_precision` is `none`: no segment times are invented and no audio exists.
- Distractor classes repeat similar quantities, dates and definitions so lexical overlap
  alone does not guarantee a correct answer.

## Item format

Each line is a frozen `G4Item` (see `tests/fixtures/g4/schema.py`):

```json
{
  "id": "es-t001",
  "language": "es",
  "partition": "test",
  "question": "...",
  "answerable": true,
  "expected_evidence": [
    {
      "class_ref": "es-algebra-01",
      "transcript_version": 1,
      "segment_ordinal": 3,
      "inicio_char": 42,
      "fin_char": 128,
      "cita_textual": "..."
    }
  ],
  "reference_answer": "...",
  "notes": "..."
}
```

Rules enforced by the loader:

- IDs are `<language>-tNNN` for test and `<language>-dNNN` for development, unique, and
  prefixed with the item language.
- The test partition has exactly 50 items per language: 35 answerable and 15 unanswerable.
- Answerable items have at least one evidence span and a `reference_answer`.
- Unanswerable items have no evidence and no `reference_answer`; their answer is absent from
  the **whole** language corpus, not only from one class.
- `cita_textual` is exactly `segment.text[inicio_char:fin_char]`, using Unicode code-point
  offsets (`fin_char` exclusive). It is a literal substring, not a paraphrase.
- Questions paraphrase the transcript; they do not copy the evidence sentence verbatim.
- IDs, counts, normalized questions and all hashes are validated before any provider call.

## Authoring and annotation guide

The corpus is intentionally written as plausible class dialogue: a topic, an explanation,
an example, a common mistake and a closing task. When extending or creating a future
version:

1. Keep one language per file and one class per line. Do not mix languages.
2. Target 10 classes, 10–14 segments and roughly 1,000–1,400 words per class so the index
   yields well above eight chunks and Recall@8 is meaningful.
3. Add distractors across classes: the same formula with another value, a similar
   definition, or a date that differs by one week. Avoid copying a sentence between
   classes.
4. Write answerable questions that require reading the class, not keyword matching alone.
   Put the supporting sentence in one segment whenever possible so the span does not
   straddle a chunk boundary.
5. Write unanswerable questions as plausible near-misses. Verify the answer is absent from
   every class of that language, including the distractor classes.
6. Annotate evidence with the exact segment ordinal and code-point offsets. Copy the
   substring from the segment text rather than retyping it.
7. Regenerate `manifest.json` after any edit and update `dataset_version`; never patch a
   published `g4-v1` in place.

The harness re-checks retrievability after indexing: every `cita_textual` must appear
literally in at least one chunk of its class, otherwise `prepare` fails with the offending
item IDs. If a span straddles a chunk boundary, shorten the span in the new dataset version
instead of changing the chunker.

## Human review protocol (G4)

The G4 gate is not self-scoring. A human reviewer must adjudicate every row produced by a
real run before `score` can publish a verdict.

1. Run `prepare`, then `run` on the `test` partition with authorized real embedding and LLM
   providers. Raw artifacts stay in `var/eval/g4/<run-id>/` and are never committed.
2. Open `review.csv` (spreadsheet copy) or `review.jsonl`. Each row has an opaque `row_id`,
   the item/citation identifiers, the quote and answer excerpt, and empty `verdict`,
   `reviewer`, `reviewed_at` and `notes`.
3. For every citation row, mark `correct` only if the quoted evidence actually supports the
   answer. Mark `incorrect` for a wrong, unsupported or misleading citation. Do not fix the
   answer text; the harness scores what the model emitted.
4. For every unanswerable row, mark `abstained` only if the answer refuses and has no
   citations. Mark `answered` otherwise, even if the answer is vague.
5. Sign each row with a stable reviewer ID and an ISO-8601 UTC timestamp. Missing, extra or
   unsigned rows make `score` exit `3` and publish nothing.
6. Two reviewers are recommended for a disputed row; a single reviewer is accepted for v1
   but must be recorded as a limitation in the report.
7. Run `score`. It publishes `REPORT.md`, `metrics.json` and
   `adjudication.redacted.jsonl` with IDs and verdicts only, never question, answer or quote
   text.

Per-language pass requires Recall@8 ≥ 0.85, citation precision ≥ 0.95, abstention ≥ 0.90
and zero unauthorized citation IDs, with at least one reviewed citation. Every enabled
language must pass; the macro average is diagnostic only and never compensates a failed
language. Thresholds use integer comparisons (30/35 and 14/15 pass; 29/35 and 13/15 fail).

Until a real authorized run and review exist, the harness and this dataset are delivered but
**G4 remains `NOT EVALUATED`**. A green fake-provider suite is not a G4 pass.

## Verification commands

```bash
cd apps/api
uv run python -m tools.eval.cli validate \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr
uv run python -m tools.eval.cli validate \
  --dataset tests/fixtures/g4/datasets/v1 --languages es,en,fr --official
uv run pytest tests/unit/test_g4_metrics.py tests/unit/test_g4_dataset.py -q
```

The first command checks schema, counts, IDs, questions, evidence spans and hashes. The
second additionally requires the exact 50/35/15 test partition. Unit tests never need a
database or a provider.
