# Reviewer evaluation design brief

`srg evaluate-reviewer` compares local models as four-way classifiers. Each
decision supplies one authoritative requirement sentence and one mock generated
statement. The reviewer classifies their relationship; it does not audit a
complete control response or propose an edit that SRG attempts to grade.

This scope is intentional. Small local models can synthesize plausible control
responses but have not reliably audited the same dense, multi-source material.
The smoke profile instead measures the primitive needed by a future
statement-by-statement review pipeline.

Production `srg generate --review` is unchanged. This command does not split a
live response into statements or feed classifications back through the
generator.

## Reviewer input and output

Every call contains only:

```json
{
  "control_id": "SI-5",
  "grounding_information": "Incoming security alerts must be reviewed within 24 hours of receipt.",
  "mock_generated_draft": "The security team completes its review of each incoming security alert within 72 hours after receipt."
}
```

There are no generator instructions, analyst notes, retrieved customer
standards, NIST excerpts, private system context, validations, or surrounding
draft paragraphs. `grounding_information` is the complete authoritative basis
for that decision.

The response has one required field and one optional field:

```json
{
  "classification": "contradicts_requirement",
  "constructive_feedback": "The generated deadline is 72 hours rather than 24 hours."
}
```

The allowed classifications are:

- `supported`: the generated statement faithfully expresses the requirement;
- `missing_required_information`: it addresses the requirement but omits a
  material qualifier or value;
- `contradicts_requirement`: it conflicts with a material qualifier or value;
- `adds_unsupported_information`: it asserts a fact outside the supplied
  requirement, including content belonging to another control.

An unrelated statement is classified as unsupported. It is not also penalized
for failing to repeat the grounding requirement.

`constructive_feedback` is optional and deliberately unscored. It is preserved
verbatim in `critiques.md` so a human can understand what the reviewer said, but
it cannot improve or reduce any metric in `summary.txt`.

## Smoke corpus

The corpus has two fictional requirements and five single-sentence, single-
claim scenarios for each:

| fixture condition | expected classification |
|---|---|
| `clean` | `supported` |
| `missing_required_information` | `missing_required_information` |
| `incorrect_required_information` | `contradicts_requirement` |
| `unsupported_claim` | `adds_unsupported_information` |
| `wrong_control_content` | `adds_unsupported_information` |

The unsupported implementation and wrong-control cases remain separate fixture
types because they may expose different model behavior, although the correct
classification is the same. Each is a standalone logical statement; no fixture
joins a supported claim and a bad claim with “and.” Clean fixtures explicitly
describe completion so they cannot be read as merely beginning work before a
deadline.

That produces ten decisions per reviewer and twenty calls in a normal
candidate-versus-comparison run.

## Deterministic scoring

SRG already knows the expected classification for every fixture. A response is
correct only when its parsed `classification` exactly matches that answer. No
LLM grades another LLM, and SRG does not interpret the feedback text.

The primary accuracy table places correct and wrong classifications side by
side, followed by invalid or empty outputs. Scenario-specific behavior—including
clean restraint—is already clearer in the classification-detail table, so it is
not duplicated as another aggregate column.

The per-scenario table shows the expected classification and whether each model
matched it. There is no separate failure-profile table and no action,
evidence-quote, targeted-fix, or serious-finding score. Those concepts either
duplicate the detail table, require semantic interpretation, or conflate
classification with the model's choice of editing vocabulary.

Distinctness remains a secondary collapse diagnostic. It is calculated from
valid classification values only, so varied constructive prose cannot make a
constant classifier look more capable. Invalid and empty outputs are excluded.

## Human and machine artifacts

`critiques.md` places the authoritative requirement, mock generated statement,
expected classification, reviewer classification, correctness result, and
optional constructive feedback together for every decision. The feedback is
clearly labeled as retained but not scored.

`results.json` contains the frozen suite inputs, raw model output, parsed
classification, unscored feedback, timings, hidden-reasoning counts, and model
residency snapshots. `answer-key.md` lists each fixture condition and its
expected classification. The newest 20 recognized evaluation runs are retained;
unrelated directories are never removed.

## Operational measurements and limits

The command still measures response time, output size, hidden reasoning, token-
ceiling hits, and whether the reviewer remains resident alongside the generation
and embedding models. Those two other models are loaded but never prompted.

Each response is capped at 3072 output tokens to bound models that fail to stop
or spend the entire budget reasoning. Empty output is always invalid; it can
never be mistaken for restraint.

The command does not measure:

- full-response auditing;
- statement extraction or requirement-to-statement matching;
- whether a generator applies a classification successfully;
- whether constructive feedback is accurate, helpful, or safe;
- prose quality or style; or
- statistical reliability beyond this small comparison sample.

Suite version 4 is not numerically comparable to earlier reviewer-evaluation
runs. Those runs remain useful as qualitative evidence only.

## Calibration procedure

1. Compare the configured reviewer with a candidate model.
2. Confirm both models receive ten decisions and no invalid or empty outputs.
3. Compare overall accuracy, clean restraint, and the scenario-level table.
4. Read `critiques.md` to understand incorrect classifications and any feedback
   the model volunteered.
5. Repeat runs before treating a small difference as stable model behavior.
6. Add corpus examples only after the classifier contract is calibrated, while
   preserving one authoritative sentence and one logical generated statement
   per decision.
