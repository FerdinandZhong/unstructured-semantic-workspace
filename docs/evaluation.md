# Evaluation

This page reports the paper's results with their scope.
Evidence links reach useful precision with a low false-link rate on unseen synthetic packs.
The catalog is below a usable level on recall.
The question-answering path does not meet its pass bar on any unseen exam.

!!! warning "Read the scope of every number"
    The evaluation sets are small. One AI annotator wrote each set.
    There is no second annotator and no agreement statistic.
    Differences of a few points are not meaningful. No confidence intervals are reported.
    The sets cover two demo datasets. The paper does not claim state-of-the-art results.

## Metrics

Each gold row has a passage, a set of acceptable definitions (empty when none fits), a relation, and optionally a value and an entity.
The selected definition is the first link, or none.

| Metric | Definition |
| --- | --- |
| Precision | Right selections over all selections |
| Recall | Right selections over rows with an acceptable definition |
| False-link rate | Rows with no acceptable definition that still got a link, over all such rows |
| Offered@k | Rows whose right definition is in the candidate list of size k, over positive rows |
| Value and entity accuracy | Exact-match rates on rows that carry them |

Canonical variants compare canonical definitions on both sides.

## Protocol

- An annotator who does not read the pipeline code writes each gold set.
- Implementers never open held-out labels.
- Settings are frozen before a held-out run.
- The controller runs each held-out set once.
- A set that has been read is relabelled development data.

## Pipeline A, held-out

Gold set A2: 82 anchors, 53 positive. Run once.

| Method | Precision | Recall | False-link rate | Relation acc. | Value acc. | Entity acc. |
| --- | --- | --- | --- | --- | --- | --- |
| Threshold and rules | 0.29 | 0.19 | 0.31 | 0.00 | — | — |
| Interpreter, `mistral-small` | **0.70** | **0.53** | 0.10 | **0.67** | **0.41** | **0.45** |
| Interpreter, `mistral-medium` | 0.58 | 0.26 | **0.07** | 0.52 | 0.34 | 0.40 |

Per dataset, small model: finance precision 1.00 (18 selected), recall 0.67; manufacturing precision 0.45 (22 selected), recall 0.38.

Later changes added guard G6, the canonical map, multi-link output and chunk-level entity keys.
On the same set, canonical precision is then 0.82 and value accuracy 0.63.
The set was development data by then, so these numbers are optimistic.

### Selection without a model

Held-out, run once (82 anchors, first gold set). Selection by score threshold and fitted compatibility rules.

| Arm | Precision | Offered@12 |
| --- | --- | --- |
| Keyword only | 0.40 | 0.49 |
| Hybrid | 0.27 | 0.73 |
| Hybrid with passage context | 0.21 | 0.76 |

The same rules reached 0.95 precision on the implementer's own examples.
This gap motivated the model decision step and the held-out protocol.

## Pipeline A, unseen packs (test E24)

Each method run once with frozen settings.
The two packs are generator-made planted truth. Only A3 is independently annotated.
Precision / recall / false-link rate.

| Method | Finance pack (163 sentences, 104 positive) | Manufacturing pack (212 sentences, 144 positive) | Set A3 (52 rows, 13 positive) |
| --- | --- | --- | --- |
| Hybrid-12 | 0.72 / 0.60 / 0.08 | 0.76 / 0.56 / 0.07 | 0.21 / 0.31 / 0.28 |
| Hybrid-30 | 0.63 / 0.53 / 0.10 | 0.84 / 0.59 / 0.06 | 0.24 / 0.31 / 0.26 |
| Expanded query | 0.74 / 0.62 / 0.07 | 0.74 / 0.56 / 0.12 | 0.21 / 0.23 / 0.21 |
| Two-step (default) | 0.73 / 0.59 / **0.03** | **0.90** / **0.62** / **0.01** | 0.31 / 0.31 / **0.18** |

- Two-step has the lowest false-link rate on all three sets.
- Its cost is 1.7 calls per chunk, against 1.0 for hybrid-12.
- Commitments are the weakest category. Every method links 0.00 to 0.12 of the finance commitments and 0.34 to 0.48 of the manufacturing commitments.
- On A3, whose passages come mostly from product manuals, every method is poor. With 13 positive rows these numbers are rough.

### Where commitment recall is lost

The shortlist step drops the right definition.
It reaches the decision call for 0.55 to 0.61 of commitment passages.
The wide first-step list holds it for 0.94 to 0.95.
This is the implementer's measurement.

Two switches aimed at this gap were tried and rejected.
Synthetic packs, second use (development data).

| Setting | Finance pack | Manufacturing pack |
| --- | --- | --- |
| Default (`interp-v3`, shortlist only) | 0.73 / 0.59 / 0.03 | 0.90 / 0.62 / 0.015 |
| `interp-v4` | 0.62 / 0.57 / 0.10 | 0.83 / 0.63 / 0.04 |
| Shortlist + 5 candidates | 0.62 / 0.53 / 0.10 | 0.82 / 0.60 / 0.07 |

The commitment gap stays open.

## Pipeline B, concept gold set

39 concepts. Manufacturing has 21 gold concepts and 17 gold links.
The first row is held-out. Later rows are development data.

| Design | Mfg. predicted | Mfg. correct | Mfg. precision | Mfg. recall (of 17) | Fin. correct (of 7) |
| --- | --- | --- | --- | --- | --- |
| Free induction, model merge, model link | 4 | 1 | 0.25 | 0.06 | 0 |
| The same with two levels and linked entities | 15 | 8 | 0.53 | 0.47 | 2 |
| Anchored on Output A, narrow corpus, hybrid-12 | 10 | 7 | **0.70** | **0.41** | 0, not measurable |
| Anchored, wider corpus, two-step | 9 | 5 | 0.56 | 0.29 | not scored |
| The same with later rule changes | 11 | 5 | 0.45 | 0.29 | not scored |

The score did not improve on the wider corpus.
The corpus and the candidate method changed at once, so the cause is not isolated.
One cause is visible: two-step abstains on contract headers and promise clauses.
Pipeline B can only anchor what Pipeline A links.

Agreement between two resampled induction runs: manufacturing 0.83 (free, coarse) and 1.00 (residual only); finance 0.36 to 0.42.
The finance corpus is too small and form-dominated for this number to carry weight.

## Table rows

The table-write feature has no measured accuracy result. See [Table rows](output-c.md).

## Question answering, unseen exams

Four exams, each with 30 questions, 15 per dataset: 4 database only, 4 documents only, 4 both, 3 with no supported answer.
Each was written by an AI author who did not implement the answer path, and run once.
The pass bar is 14 of 15 per dataset with every no-supported-answer question refused.

| | Exam 1 | Exam 2 | Exam 3 | Exam 4 |
| --- | --- | --- | --- | --- |
| Finance (of 15) | 3 | 7 | 9 | 9 |
| Manufacturing (of 15) | 8 | 7 | 9 | 8 |
| Total (of 30) | 11 | 14 | 18 | 17 |
| Design | first | redesign | round 3 | round 4 |

No exam meets the pass bar.
All four exams are spent. A fifth independent exam is needed for the next honest measure.

After rounds 5 and 6 of fixes, a replay of exam 4 gives 23 of 30.
All four exams were development data by then. This is not evidence of unseen performance.

### Where the gap comes from

- **Wrong measure, table or record.** On exam 4, seven answers had facts or a conclusion the key rejects.
- **Right answers in another key shape.** On replay, exam 4 has 23 strict passes and 27 right regardless of shape. The strict number is the reported one.
- **Capabilities not built.** One plan holds one measure. Grouping, date arithmetic and relative phrases such as "last month" are not supported.

On the development set `dev_wide` (166 questions), `mistral-small-latest` answered 109 to 110 and `mistral-medium-latest` 115.
About one in five reworded questions changed outcome on the small model (11 to 12 of 54).

## Limits

- Small sets, one annotator each: 82 anchors, 39 concepts, four exams of 30 questions, 166 development questions.
- Only the first row of each results table for gold set A2 and the concept set is held-out.
- Two demo datasets. The manufacturing documents total about 7 KB. The unseen packs are generated from data rows, so their wording is more regular than real documents.
- The constants (k, the score clips, the 0.90 similarity, the support minima and the vote threshold) were chosen once and are untuned.
- Several timings and inspections are implementer claims.
