# NLI as a second reviewer: trialled and removed

## Status

**Removed.** An NLI cross-encoder was added as an independent second reviewer
for `srg evaluate-model --profile smoke`, measured against real runs, and taken
back out. This note records why, so the idea is not retried blindly.

## What was tried

`cross-encoder/nli-deberta-v3-xsmall` (Apache-2.0, 70.8M parameters) running as
a CPU-only ONNX graph through `onnxruntime`, chosen because it is a different
architecture from a different provider trained on a different objective, so its
errors should not correlate with the Ollama reviewer's.

Three signals were planned. Two were removed before shipping and the third was
removed after measurement.

## Why NLI never worked

It looked good on a 12-pair validation set: 5 true positives, 0 false
positives. **That validation set was too easy.** Its negatives were topically
*unrelated* (backups versus audit records), so a 0.30 relatedness gate
separated them trivially. Real negatives share heavy domain vocabulary — every
sentence in an AC-2 response says "accounts".

Measured across six real evaluation runs:

| | count |
|---|---:|
| Distinct conflicts reported | 20 |
| True positives | **0** |
| False positives | **20** |

The detector
would have become silent rather than useful, while still costing a 284 MB
download, ~630 MiB of process RSS, four newly declared dependencies, a setup
step, a cleanup step, and a preflight failure mode. Memory is this project's
tightest constraint.

## What actually shipped

Two changes that shipped alongside the trial were kept, because they are what
actually improved the reviews:

- **SRG derives the category** on the smoke profile. The reviewer model's
  overall `assessment` is recorded as `reviewer_assessment` but never decides
  the result (`derive_assessment` in `model_evaluation.py`).
- **Validation headings left in the narrative are an automatic failure**
  (`count_narrative_validations`), in both profiles.

## If this is revisited

- Validate against **topically related** negatives drawn from real runs, not
  synthetic unrelated pairs. That was the mistake that made the first
  validation look convincing.
- Expect negation-heavy controls (AC-2, and any "no X is deployed" analyst
  fact) to be the worst case, not the easy case.
- An entailment model trained on summarization faithfulness rather than
  SNLI/MNLI would be a better starting point than a general NLI cross-encoder.
- Confirm a real true positive exists before wiring any signal into the
  categorization.
