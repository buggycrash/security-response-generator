# NLI second reviewer: trialled and removed

## Status

**Removed.** An NLI cross-encoder was added as an independent second reviewer
for `srg evaluate-model --profile smoke`, measured against real runs, and taken
back out. This note records why, so the idea is not retried blindly.

The two changes that shipped alongside it were kept, because they are what
actually improved the reviews:

- **SRG derives the category** on the smoke profile. The reviewer model's
  overall `assessment` is recorded as `reviewer_assessment` but never decides
  the result (`derive_assessment` in `model_evaluation.py`).
- **Validation headings left in the narrative are an automatic failure**
  (`count_narrative_validations`), in both profiles.

## What was tried

`cross-encoder/nli-deberta-v3-xsmall` (Apache-2.0, 70.8M parameters) running as
a CPU-only ONNX graph through `onnxruntime`, chosen because it is a different
architecture from a different provider trained on a different objective, so its
errors should not correlate with the Ollama reviewer's.

Three signals were planned. Two were removed before shipping and the third was
removed after measurement.

## Why coverage and grounding never worked

Measured against the real model on real fixtures:

- **Coverage does not survive the requirement/compliance gap.** "The State
  *requires* alerts to be reviewed within 24 hours" does not entail "Alerts
  *are* reviewed within 24 hours by the operations team" — a requirement is not
  a statement that it was met, and the draft adds an actor the source never
  mentions. Both are correct NLI readings. Entailment measured ~0.00 on drafts
  that plainly covered the requirement.
- **Grounding is defeated by ordinary composition.** A claim the customer
  standard *literally states* scored **0.004**, because the draft sentence
  combined it with a fact from elsewhere.
- **A "topical engagement" fallback (`1 - neutral`) was anti-correlated.**
  Sentences that ignored a requirement scored *higher* (0.563) than sentences
  that addressed it (0.006).

Shipping either would have flagged nearly every response as uncovered and
unsupported — worse than the over-harsh reviewer the work existed to correct.

## Why conflict detection was removed

Conflict detection shipped, gated on lexical relatedness because raw
contradiction scores are unusable on their own (unrelated sentence pairs score
0.97–0.99, an SNLI/MNLI annotation artifact where "different specifics" was
labelled contradiction).

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

Representative false positives, all complementary rather than conflicting:

```
"The security operations team continuously monitors high-priority alerts."
"Remediation and closure tracking are performed by the engineering lead."

"The Platform Team manages workforce accounts."
"Shared or generic workforce accounts are strictly prohibited across the system."
```

25 of 34 hits came from the single `ac2-negative-fact` case, because it is
built around a *negative* fact ("there are no shared or group accounts") and
NLI models are hypersensitive to negation. Several `si5-context` hits fired
against validation text that had leaked into the narrative — a defect
`count_narrative_validations` now catches directly and reliably.

Recall was also poor. A genuine policy contradiction —

```
"Shared and group accounts are prohibited and are not deployed."
"Shared administrator accounts are used for emergency access."
```

— scored only 0.062.

## Why it was not salvaged

Two gates would have eliminated all 20 false positives while keeping the
synthetic true positives: raising relatedness to 0.55, or additionally
requiring differing numeric/frequency tokens. Both were rejected because
**either one would have fired zero times across six real runs** — the detector
would have become silent rather than useful, while still costing a 284 MB
download, ~630 MiB of process RSS, four newly declared dependencies, a setup
step, a cleanup step, and a preflight failure mode. Memory is this project's
tightest constraint.

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
