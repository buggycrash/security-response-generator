# Standard generation-model evaluation profile

## Status and purpose

This is an implementation brief for the planned `standard` profile of
`srg evaluate-model`. The current `smoke` profile is an MVP for developing and
debugging the evaluation process. The standard profile is intended to provide
enough task breadth and repeated trials to decide whether a candidate deserves
blinded human consideration as a future shipped generation-model default.

The standard profile must remain advisory. It can make a candidate ineligible
and can identify a candidate worth human review, but it must not automatically
change SRG's default model.

Reviewer-model qualification is a separate future capability. The standard
profile described here evaluates generation models only and continues to use a
fixed reviewer as evaluation infrastructure.

## Smoke versus standard

| Dimension | Smoke profile | Planned standard profile |
|---|---:|---:|
| Primary use | Develop and debug the harness | Evaluate a candidate for possible default-model qualification |
| Fictional tasks | 3 | 10 |
| Trials per task and model | SI-5 has 3; AC-2 and SC-8(1) have 1 | 3 for every task |
| Generation models | Candidate and comparison | Candidate and comparison |
| Generated responses | 10 total | 60 total |
| Grader calls per response | 2 | 2 |
| Grader calls | 20 total | 120 total |
| Expected automated runtime | Approximately 10-18 minutes | Initially estimate 60-110 minutes; calibrate after implementation |
| Quality interpretation | Development signal only | Repeated, task-balanced evidence plus mandatory blinded human review |
| Human review | Inspect priorities and spot-check prose | Review all high-risk/disagreement trials plus a blinded stratified sample |

The standard workload is six times the smoke workload. Models larger than
SRG's default, mixture-of-experts models, or models that do not fit comfortably
on the target workstation may take substantially longer.

## Command and confirmation

The planned command is:

```bash
srg evaluate-model <candidate-model> --profile standard
```

`--compare-to` remains optional and continues to default to SRG's shipped
generation model. `SRG_REVIEW_MODEL` may still select an installed local grader,
but grader experiments should not be mixed into a formal candidate comparison:
the grader name and version must remain fixed across runs being compared.

Before confirmation, the command should display at least:

- candidate, comparison, embedding, and grader model tags;
- profile and suite version;
- 10 tasks, 3 trials per task and model, 60 responses, and 120 grader calls;
- timing, memory, GPU-residency, ejection, and quality measurements;
- estimated automated runtime and the warning for larger or MoE models;
- absolute artifact parent directory;
- confirmation that only committed fictional fixtures will be used; and
- the existing default-no `Proceed? [y/N]` prompt.

After confirmation, the run remains fully noninteractive and safely
interruptible.

## Task bank

### Design requirements

The suite should contain ten versioned, committed, fictional cases. Each case
must use the same input shape as the smoke fixtures:

- exact control ID and NIST control text;
- analyst context;
- zero or more customer-standard chunks;
- zero or more private-context chunks; and
- the shared process rubric.

The generation models must receive identical frozen inputs. The suite must not
read the active engagement, live Chroma collections, or customer data. Cases
should test behavior rather than reward memorized wording, and the common rubric
must continue to explain *how* to evaluate rather than restating case-specific
answers.

Each fixture should also gain metadata useful for reporting and suite
maintenance, such as a suite version, task tags, source-presence flags, and a
short statement of the behavior the case is intended to exercise. This metadata
must not be sent to either generation model.

### Proposed coverage

Keep the three smoke cases as the first three standard cases:

1. **SI-5 multi-source reconciliation.** Slightly misaligned analyst, customer,
   baseline, and private information; tests analyst-context retention,
   customer-standard interpretation, operational grounding, and consistency.
2. **AC-2 negative analyst fact.** Tests whether “no shared or group accounts”
   is incorporated without incorrectly declaring the control inapplicable.
3. **SC-8(1) simple enhancement scope.** Tests a straightforward TLS 1.3 fact,
   exact enhancement scope, and resistance to unrelated “kitchen sink” content.

Add seven cases covering distinct failure modes rather than seven variations of
SI-5. The final control IDs should be chosen when the fictional fixtures are
authored, but the suite should include these archetypes:

4. **Customer-specific parameter preservation.** Multiple authoritative roles,
   frequencies, or timeframes where omitting all customer content is a hard
   failure and partial coverage requires edits.
5. **Relevant private implementation context.** Material architecture and
   operations that should appear in the draft but whose partial omission is not
   a hard failure.
6. **Large or noisy private context.** Relevant facts mixed with irrelevant,
   explicitly old, or conflicting details; tests selection and source
   precedence without rewarding wholesale copying.
7. **No customer standard.** Ensures an empty customer source is treated as
   `not_provided`, not as missing required content.
8. **No private context.** Ensures the model can produce a grounded baseline and
   analyst-driven response without inventing implementation details.
9. **Control-family neighbor trap.** Source material contains true information
   about a related control; tests exact scope and penalizes material TMI.
10. **Validation grounding.** Tests whether suggested evidence is plausibly
    obtainable and tied to narrative claims without treating a validation as
    proof or allowing it to repair a missing narrative fact.

Candidate control families for the seven new cases include IA, AU, CM, IR, CP,
RA, and an additional SC control. The exact controls should be selected for
clear, non-overlapping behaviors and then calibrated with known model outputs.

## Trial schedule and model lifecycle

Run every case three times per generation model using fixed, recorded seeds,
initially 42, 43, and 44. This produces:

```text
10 tasks × 3 seeds × 2 generation models = 60 responses
60 responses × 2 isolated grader calls = 120 grader calls
```

The two grader calls remain:

1. a bounded, temperature-zero analyst-inclusion check that receives only the
   analyst context and narrative and must provide verifiable narrative evidence;
2. an independent broader assessment that receives customer, private, baseline,
   narrative, and validation content but not the analyst context or opposing
   response.

Every call remains stateless: no conversation history, prior grade, cached
judgment, opposing response, or preceding trial is supplied. The larger profile
scales by making more bounded independent calls, not by constructing a larger
cross-trial prompt.

For each generation model:

- begin with the model verified as unloaded;
- warm `embeddinggemma` before the first generation;
- measure the first request as cold and all later requests as warm;
- sample Ollama memory and GPU residency after every generation;
- keep the generation and embedding models resident throughout that model's
  block; and
- stop immediately, preserve partial artifacts, and mark the run failed if
  Ollama ejects either required model.

The implementation should record model-block order. A later calibration study
can determine whether formal qualification requires counterbalancing candidate
and comparison order across repeated standard runs.

## Automated measurements

### Performance and workstation fit

Retain the MVP measurements and raw evidence:

- cold wall time, with the current hard threshold of less than 75 seconds;
- every warm wall time, with the current target of less than 40 seconds;
- average, median, p95, minimum, and maximum warm time;
- average and peak Ollama-reported model allocation;
- average combined allocation with the embedding model;
- average GPU allocation and full-GPU-residency rate;
- model-ejection checkpoints; and
- process-table polling time excluded from generation timing.

The existing 7 GiB memory and incomplete-GPU-residency highlighting remains a
warning unless qualification calibration establishes a reason to make either a
hard gate.

Before implementation, decide whether a single warm outlier should fail the
standard profile or whether the gate should use p95 under 40 seconds with a
separate absolute maximum. Preserve all timings either way; do not silently
discard outliers.

### Source coverage and deterministic policy

Retain the current policy for every trial:

- missing analyst context in the narrative is `not_viable`;
- an unverified positive analyst result cannot remain `viable`;
- when customer chunks exist, no customer coverage is `not_viable` and partial
  customer coverage requires `material_edits`;
- missing or partial relevant private-context coverage requires
  `material_edits` but cannot alone make a response `not_viable`;
- one explicit placeholder prevents `viable`, and two or more are
  `not_viable`; and
- material wrong-control scope drift prevents `viable`.

Continue reporting raw grader output, policy adjustments, analyst evidence
quotes and verification, placeholder counts, forced-completion calls, and
contradictory grader findings.

### Statistical aggregation

The standard profile should add task-balanced statistics while retaining the
underlying per-trial findings:

- counts and rates of `viable`, `material_edits`, `not_viable`, and
  `inconclusive` per model and per task;
- analyst-missing, analyst-unverified, customer-none, customer-partial,
  private-none/partial, placeholder, forced-completion, and scope-drift rates;
- seed consistency for each task and model;
- paired candidate-versus-comparison results for each identical task and seed;
- paired win, loss, and tie counts using the assessment severity ordering;
- macro averages that give each task equal weight, so three easy tasks cannot
  hide one systematically failed task; and
- confidence intervals or bootstrap intervals for overall paired differences,
  clearly labeled as descriptive evidence rather than proof of model quality.

Per-task aggregation may remain conservative: any `not_viable` trial makes that
task aggregate `not_viable`. The overall standard-profile report should not
collapse the ten task aggregates into a single unexplained pass/fail label.

## Human review

Automated review still does not measure prose quality, clarity, usefulness to an
analyst, or assessor effort reliably. Reading all 60 responses is also unlikely
to remain practical. Generate a blinded review set containing:

1. every candidate/comparison pair where their automated assessments differ;
2. every `not_viable`, analyst-unverified, contradictory, or otherwise
   high-priority finding;
3. at least one seeded pair from every task; and
4. a deterministic random sample of remaining automated ties.

The worksheet should ask the human reviewer to judge:

- factual and control correctness;
- retention and useful placement of analyst context;
- customer-standard and private-context use;
- unsupported claims, scope drift, and unnecessary TMI;
- validation usefulness;
- prose clarity and editing burden; and
- which blinded response is preferable, or whether they are effectively tied.

Record the sampling rule, selected trial IDs, human decisions, and answer key.
Open the answer key only after completing the blinded judgments.

## Qualification decision

The first implementation should report evidence, not pretend the acceptance
thresholds are already calibrated. Use runs of the shipped default and several
known unsuitable and plausible models to compare automated findings with
blinded human judgments.

After calibration, a candidate should be eligible for human consideration only
when all hard requirements are satisfied, including:

- cold and warm performance gates on the target workstation;
- no generation or embedding model ejection;
- no unexplained regression in analyst-context omission, total customer-source
  omission, repeated placeholders, or other deterministic hard failures;
- no task with a systematic critical regression hidden by better performance on
  unrelated tasks; and
- sufficient completed and verifiable grader results.

Promotion must additionally require blinded human review showing no material
regression in correctness, scope, prose usefulness, or analyst editing burden.
The repository owner remains responsible for changing the shipped default.

## Artifacts and terminal output

Reuse the smoke artifact structure and retention behavior, adding:

- suite and fixture version identifiers;
- a compact machine-readable metrics section in `results.json`;
- per-task statistics and paired outcomes;
- the human-review sampling manifest;
- enough metadata to reproduce task, seed, model order, prompt inputs, and
  deterministic policy decisions; and
- a standard-profile label that does not say `SMOKE EVALUATION`.

Keep terminal output compact. Show performance/workstation fit, model-level
quality statistics, ten task aggregates, hard-failure counts, and human-review
priorities. Leave all 60 trial details in artifacts rather than printing them.

## Implementation sequence

1. Add a versioned standard fixture file with ten cases and metadata.
2. Generalize profile loading and schedules instead of duplicating the smoke
   execution path.
3. Add `--profile standard` preflight, confirmation counts, duration estimate,
   and profile-specific report labels.
4. Run three seeds for every task while retaining the current model lifecycle,
   residency checks, interruption behavior, and 20-run retention.
5. Add statistical aggregation and compact standard terminal tables.
6. Add deterministic blinded human-review sampling and record its manifest.
7. Calibrate automated results against human judgments from known models.
8. Document and test final qualification gates only after calibration evidence
   supports them.
9. Keep reviewer-model evaluation out of this implementation; design it as a
   separate suite later.

## Definition of done for the first standard-profile release

- `srg evaluate-model MODEL --profile standard` runs ten fictional tasks with
  three fixed seeds for both generation models.
- The confirmation plan accurately shows 60 responses, 120 grader calls, and a
  calibrated runtime estimate.
- Runs remain local-only, stateless, noninteractive after confirmation, safely
  interruptible, and resumable only by starting a fresh isolated run.
- Ejection, timing, memory, source coverage, placeholders, grader contradictions,
  and incomplete operations retain reproducible evidence.
- Terminal summaries stay compact while artifacts retain every trial.
- Blinded human-review sampling covers all tasks and all automated high-risk
  findings.
- Offline tests cover profile selection, counts, schedules, aggregation,
  sampling, interruption, ejection, retention, and backward compatibility of
  the smoke profile.
- Documentation explicitly distinguishes “eligible for human consideration”
  from “approved as SRG's new default.”
