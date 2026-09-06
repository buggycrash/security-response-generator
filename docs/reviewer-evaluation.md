# Reviewer evaluation design brief

`srg evaluate-reviewer` measures how well a local model performs SRG's
**review/revision critique** role. This document explains what it measures,
why those things and not others, and what the numbers cannot tell you.

## Why this exists

SRG could already evaluate generation models (`srg evaluate-model`) but had no
way to evaluate a reviewer, despite `SRG_REVIEW_MODEL` being a documented,
user-facing choice. Evidence that reviewers vary a lot was anecdotal.

One run made the gap concrete. With `granite4.1:3b` configured as the grader,
a standard `evaluate-model` run produced a constant `not_viable` verdict on
60 of 60 responses, an empty `strengths` array on 60 of 60, and never once
emitted `customer_standard_coverage: full`. Those are degenerate outputs, not
judgments, and nothing in SRG flagged them. The deterministic layer added in
`derive_assessment` absorbed the damage — which is exactly the problem:
`evaluate-model`'s grading logic exists to *compensate* for reviewer defects,
so reusing it to measure reviewers would measure the compensation.

## What the reviewer actually does

The reviewer is **not a classifier**. In `_review_and_revise` (`cli.py`) it
emits a free-text `critique` string (`REVIEW_SCHEMA`,
`generation/review.py`) which a *separate generator* then executes under the
instruction to produce a revision "correcting every valid issue"
(`revision_instruction`). This runs twice per response, unconditionally for
`bulk-generate` and opt-in for `srg generate --review`.

That consumer relationship produces the governing asymmetry of the whole
design:

> **A missed defect leaves one flaw. An invented defect creates one**, because
> the generator complies.

So for a reviewer, **precision outranks recall** — the opposite of a grader.
An over-flagging grader mislabels a result; an over-flagging reviewer corrupts
a good draft. This is why restraint on clean drafts is weighted as heavily as
detection on defective ones, and why the corpus contains a `clean` condition
at all.

### The report is ordered to reflect this

Invention is reported **before** detection, under a "Bottom line" table that
leads with unwarranted changes. This is deliberate. An early run compared two
weak reviewers where the more aggressive one found 7 of 14 defects against the
other's 5 — and reading detection first made it look like the better choice,
when it had also demanded 20 changes on two drafts that had nothing wrong with
them. Its critiques claimed content was missing that was present verbatim:

> "it omits explicit mention that CISA advisories are forwarded ... using
> internal controlled channels"

against a draft reading *"forwarded to all system owners through internal
controlled channels."* The other reviewer, on the identical draft, quoted that
sentence and marked the fact covered.

A reviewer that finds more defects while demanding more unwarranted changes is
the worse choice, and the report should not require careful reading to see that.

### The headline metric: unwarranted changes

Exact ground truth is what makes this measurable. Every fixture draft carries
**at most one** defect, so at most one demanded change is warranted — one when
the reviewer found the seeded defect, none otherwise. Everything beyond that
asks the generator to alter content the fixture says is already correct:

```
unwarranted = Σ max(0, directives − (1 if the seeded defect was found else 0))
```

It is reported as a total and as a ratio against real finds. The ratio is `0`
when nothing was invented — a silent reviewer is useless but harmless, and must
not share a score with one that invents freely — and infinite only in the
genuinely worst case, where a reviewer demanded changes and found nothing.

Its limitation: a reviewer could flag something real that is not the seeded
defect. The fixture drafts are otherwise fully supported by their sources, so
this is rare by construction, but it is not impossible.

## Where the rubric comes from

`REVIEW_SYSTEM_INSTRUCTION` is, read carefully, a list of previously observed
reviewer failures. Every "never do X" is a scar:

- never suggest the response add coverage of other controls
- never write "I will revise…" (the reviewer does not produce the revision)
- never hedge with "most facts are present" or "largely addressed"
- never rewrite the response
- never ask the human analyst questions
- quote the exact missing fact and instruct the generator to add it by name

Measuring compliance with those clauses measures SRG's real contract rather
than a generic notion of good reviewing. Conveniently, every one of them is
checkable deterministically.

## Six qualities

| # | Quality | Measured by |
|---|---|---|
| 1 | **Restraint** | changes demanded on a clean draft; demands to alter correct content |
| 2 | **Detection** | seeded defects named in the critique |
| 3 | **Actionability** | directives extracted; whether the critique instructs at all |
| 4 | **Discrimination** | pairwise distinctness across critiques |
| 5 | **Containment** | violations of the prompt's explicit prohibitions |
| 6 | **Cost** | coexistence memory first, latency second |

### A note on cost

The number that matters for a reviewer is **peak memory with the generation,
reviewer, and embedding models resident simultaneously** — not the reviewer's
own size. A review pass alternates generator and reviewer up to four times per
control, and `docs/technical-readme.md` documents eviction-and-reload thrash as
a real symptom on constrained hardware. A reviewer that is individually small
but tips the total over available VRAM is expensive in a way its own footprint
never shows.

To measure this honestly rather than estimate it, the run loads the generation
and embedding models and leaves them resident for the whole block. Neither is
ever prompted; they exist to create real memory pressure.

## Anti-circularity: no LLM grades the reviewer

Every check is a deterministic text transform
(`reviewer_evaluation_scoring.py`). This is a hard constraint. An LLM judge
would reintroduce exactly the defect being measured, and the NLI alternative
was built, measured, and rejected — see
[`model-evaluation-nli-findings.md`](model-evaluation-nli-findings.md), where
conflict detection scored 0 true positives against 20 false positives on real
data.

The consequence is that the scoring module has no I/O and no Ollama import, so
it is fully unit-testable offline.

## The corpus

`evaluation_data/reviewer_critique_smoke.json` holds two hand-authored cases.
Each pairs one clean draft with seven variants, each carrying **exactly one**
seeded defect, so ground truth is exact by construction rather than inferred.
Drafts are stored as `RESPONSE_SCHEMA` objects because that is precisely what
`assemble_review_messages` hands the reviewer in production.

| condition | seeded defect | prompt clause it exercises |
|---|---|---|
| `clean` | none — restraint control | — |
| `dropped_analyst_fact` | one analyst fact removed | "Analyst fact checklist" |
| `wrong_customer_parameter` | 24h → 72h, 15d → 45d | "conflicts with authoritative customer standards" |
| `unsupported_claim` | invented tool and metric | "unsupported claims" |
| `wrong_control_content` | a paragraph from a neighbouring control | "Control scope creep" |
| `omitted_control_clause` | one clause of *this* control dropped | "missing coverage of THIS control's own clauses" |
| `weak_validation` | a validation untied to any narrative claim | "weak or unsupported screenshot validations" |
| `narrative_validations` | a validation heading left in the narrative | structural |

The two cases are chosen for defect surface: `si5-alert-handling` supplies
customer, private, and analyst material so all eight conditions are injectable;
`ra5-scan-scope` reuses the neighbour-trap idea from the generation suite, where
the private context contains *true* information belonging to RA-3 and SI-2, so
catching the scope creep requires discipline rather than fact-checking.

`tests/test_reviewer_evaluation_fixtures.py` pins the corpus invariants,
including that omission markers really are absent from the defective draft and
present in the clean one. An authoring slip would not crash anything — it would
silently produce a run whose numbers mean nothing.

## Scoring mechanics

### Directive extraction

The prompt tells the reviewer to phrase every instruction as something the
generator must do ("State X explicitly," "Add Y," "Correct Z"), so compliant
critiques are lexically regular. `extract_directives` matches a sentence-initial
imperative from a fixed verb set, or a modal requirement ("the response should
include…"), and labels which pattern fired.

One extractor powers three metrics: restraint (count on a clean draft should be
zero), over-flagging (count against a single seeded defect), and actionability.

Two deliberate wrinkles:

- **`State` is guarded.** It is both an imperative and, in this domain, an
  extremely common proper noun ("the State ISO", "State SOC"). A unit opening
  with a capitalized `State` followed by another capitalized word is treated as
  a noun phrase. Without this, every critique that merely mentions the customer
  would inflate the directive count.
- **Semicolons do not split sentences.** Reviewers routinely join an
  instruction to its justification with one ("Correct the window; the standard
  requires 24 hours"), and splitting there strips the imperative head off the
  directive.

### Marker matching

`detect_markers` is a list of alternative token groups; a defect counts as
detected when every token of any one group appears. This is not a shortcut:
`REVIEW_SYSTEM_INSTRUCTION` already requires the reviewer to quote the exact
missing fact and name it, so marker matching measures literal compliance with
the stated contract.

### `must_not_flag` is clean-only

A directive naming content the sources already support is a hard false
positive. This is evaluated **only** on the `clean` condition, because on a
seeded draft a correct critique legitimately quotes the right value while
naming the wrong one ("the draft says 72 hours but the standard requires 24
hours").

### `other_control` counts additions only

Catching scope creep *requires* naming the control the stray content belongs to
("remove this, it belongs to RA-3"). The prompt prohibits suggesting the
response *add* other-control coverage, not naming other controls at all. So
only an additive directive naming a foreign control counts as a violation, and
a removal verb in the same directive wins.

### The token ceiling

Each critique is capped at `REVIEWER_MAX_TOKENS` (1024). This is not a
performance tuning knob — it is a correctness requirement discovered the hard
way.

Without a cap, a reviewer that fails to stop generates until it exhausts
`num_ctx`, at which point Ollama begins **shifting the context window** and the
request never returns. Observed with `phi4-mini`: 59,000 tokens decoded over
15 minutes 49 seconds on an ordinary draft, at a healthy 62 tokens/second and
fully on GPU. Nothing was wrong with memory or throughput; the model simply
never emitted a stop. The same model had produced a perfectly good 381-token
critique on the previous draft.

In an unattended 32-call batch, one such model stalls the entire run and
produces no measurement at all. The cap converts that hang into a data point:
reaching it is recorded per critique and reported as **"Ran to token ceiling"**,
because a reviewer that cannot stop is a bad reviewer and that is exactly what
this command exists to surface.

### Sizing the ceiling: hidden reasoning

Choosing that cap is subtler than it looks, and the first attempt (1024) was
wrong in an instructive way.

**Ollama bills hidden reasoning against `num_predict` but reports only content
tokens in `eval_count`.** A thinking-capable reviewer can therefore spend the
entire budget reasoning and return *empty content*, with the timing block
showing nothing unusual. `gemma4:e2b-it-qat` emits roughly 5,000 characters
(~1,400 tokens) of reasoning before its first content token; at a 1024 ceiling
it returned nothing on 10 of 16 drafts.

That failure is quiet and it corrupts the comparison in both directions. An
empty critique requests no changes, so it scored as **perfect restraint**; it
also names no defects, so it scored as **zero detection**. The affected model
looked simultaneously well-behaved and blind, and the candidate it was being
compared against appeared better than it was.

Two changes address it:

- The ceiling is **3072**, comfortably above reasoning-plus-critique for the
  models tested. Thinking is deliberately left *enabled*, because production
  `--review` leaves it enabled — forcing `think=False` would measure a
  configuration nobody actually runs.
- **An empty response is scored as a failure, never as restraint**, and
  reported in its own "No output at all" column. Average hidden reasoning is
  reported alongside it, since a high empty count next to heavy reasoning
  identifies the cause immediately.

Hidden reasoning is reported as a **cost** in its own right. It never reaches
the generator, but it is billed against both the token budget and the clock, so
a reviewer that thinks for 5,000 characters per critique is materially more
expensive than its critique length suggests.

> **Note on the production path.** `srg generate --review` and `bulk-generate`
> call the reviewer with no `num_predict` ceiling, so they carry the same
> exposure. `bulk-generate` is unattended, which makes it the more serious
> case. This has not been changed, because capping the production reviewer
> could truncate a legitimately long critique and that is a product decision
> rather than an evaluation one.

### The metrics are complementary by design

Detection alone is gameable: a reviewer emitting one fixed critique for every
draft can score a detection whenever that text happens to contain a marker.
**Distinctness is the cross-check** — a constant reviewer scores near 0.00,
which is the tell. Read the two columns together.

## Scope, and what is deliberately not built

**Smoke profile only.** Two cases, eight conditions, one seed, both reviewers:
32 calls, roughly 5–11 minutes. There is no `standard` profile and will not be
one until the metrics are shown to discriminate between real models. Shipping a
large run against uncalibrated thresholds would just burn tokens.

**Effect measurement is deferred.** Measuring whether the generator actually
improves after a critique (repair rate, collateral damage on clean drafts) is
the natural next layer, but `srg generate --review` already exercises that path
end to end by hand. Automating it now would duplicate existing coverage.

Two cheap seams keep that door open without any speculative code:

- `must_keep` is authored into every case (the facts a revision must not drop).
  It is unused today; retrofitting it would mean re-reading every hand-written
  draft.
- `PROFILES` is a dict with one entry and the run loop takes its shape from the
  profile, so adding a profile is data rather than a CLI change.

**The grader role is out of scope.** `SRG_REVIEW_MODEL` also drives
`evaluate-model`'s grader, but the two jobs share almost no metrics. A model
that critiques well may still grade badly, and vice versa.

## What the numbers cannot tell you

- **This measures what the reviewer says, not what the generator does with
  it.** A model could score well here and still produce critiques the generator
  mishandles.
- **Smoke scale means one observation per defect per case.** The
  candidate-versus-comparison difference is the signal; individual cells are
  anecdotes.
- **Marker matching rewards reviewers that quote specifics.** That is what the
  prompt demands, but a correct critique phrased entirely in paraphrase scores
  as a miss. Alternative marker groups mitigate this; they do not eliminate it.
- **`weak_validation` has the loosest ground truth** of the eight conditions.
  Its marker relies on the seeded validation referencing content absent from the
  narrative, which a reviewer might describe rather than quote.
- **Thresholds ship uncalibrated** — the directive-count expectations and the
  rewrite-length cutoff are first guesses. Treat early runs as calibration data,
  not verdicts.
- **Prose quality, tone, and long-document behavior are not measured at all.**

## Where this left off

Five candidate reviewers were run against `gemma4:e2b-it-qat` on 2026-09-05.
Figures below are **re-scored from the stored raw output** with the scoring as
it stands now, so they are comparable to each other; the numbers printed in
those runs' own `summary.txt` predate the truncation salvage and the expanded
imperative verb list, and are lower.

| reviewer | defects found | unwarranted changes | per real find | demanded per clean draft | notes |
|---|---|---|---|---|---|
| `gemma4:12b-it-qat` | **10 / 14** | 50 | 5.0 | 5.5 | best detection by a wide margin; 3 of 16 calls still exhausted the token budget on hidden reasoning |
| `granite4.1:3b` | 7 / 14 | **12** | **1.7** | 2.5 | best ratio, but see the caveat below |
| `gemma4:e2b-it-qat` | 5 / 14 | 28 | 5.6 | **0.5** | the shipped default; identical numbers across four runs |
| `llama3.1:8b` | 4 / 14 | 49 | 12.2 | 2.0 | writes reports, not instructions — 6 of 16 critiques tripped `rewrote_draft`, one opened with a document title |
| `phi4-mini` | 7 / 14 | **139** | **19.9** | **23.5** | invents freely; the reason the report now leads with invention |

Read alongside the owner's own reading of the critiques:

- **`gemma4:e2b-it-qat` punches above its weight** but is not a good reviewer.
  It is the most restrained of the five and perfectly reproducible, yet it
  misses 9 of 14 defects. Its value is that it rarely does harm.
- **`gemma4:12b-it-qat` is the best of the five** and still demands roughly
  five unnecessary changes per clean draft.
- **`llama3.1:8b`, `phi4-mini`, and `granite4.1:3b` are all unusable**, for
  three different reasons — report-writing, invention, and (below) terse
  confident wrongness. That they fail differently is itself the argument for
  measuring several dimensions rather than one score.

### The ratio is not yet a ranking

`granite4.1:3b` tops the headline ratio and is *not* a good reviewer. The
metric rewards terseness: a model that says little scores well whether or not
what it says is true. Granite also tripped `rewrote_draft` on 5 of 16 calls.
**Do not read the Bottom line table as an ordering until the calibration
below is done.** It is a risk indicator, not a score.

### The finding worth chasing next

Four of the five reviewers — `phi4-mini`, `granite4.1:3b`, `gemma4:12b-it-qat`
and, on other drafts, others — produce the *same* false positive on the
`si5-alert-handling` **clean** draft. They claim the analyst fact about CISA
alerts is missing:

> granite: "it does not explicitly state that CISA alerts are received by the
> State SOC and forwarded to all system owners via internal controlled
> channels"
>
> gemma4:12b: "it omits a key fact from the Analyst-Provided Facts section ...
> While it mentions CISA alerts being sent to owners, it fails to mention the
> specific role of the State Security Operations [Center]"

The draft says, verbatim: *"CISA alerts are received by the State Security
Operations Center (SOC) and forwarded to all system owners through internal
controlled channels."*

Four independent models converging on one wrong answer is much more likely to
be a property of the task than four coincidental hallucinations. Two candidate
explanations, both testable:

1. **A fixture artifact.** The analyst fact says "State SOC"; the draft
   expands it to "State Security Operations Center (SOC)". Models may be
   matching on the literal token. If so, the clean draft is not as clean as
   the corpus assumes, and part of every model's "invention" score is the
   fixture's fault.
2. **A prompt artifact.** `REVIEW_SYSTEM_INSTRUCTION`'s analyst-fact checklist
   is emphatic — "If any fact is missing, quote the exact missing fact ... Do
   not summarize this check with a vague, hedged judgment" — and may be
   leading models toward finding a missing fact whether or not one exists.

If (2) holds, it is the most valuable result this tool has produced so far,
because it is a fixable defect in the **production** review prompt rather than
a property of any model.

### What the exercise established

Reviewing is demonstrably not the same task as generating. `gemma4:12b-it-qat`
finds twice the defects of the shipped reviewer, and `phi4-mini` — a
respectable generation model — is actively dangerous in this role. Neither
ranking is predictable from generation benchmarks, which is the case for
keeping `evaluate-reviewer` a separate mode rather than a flag on
`evaluate-model`.

## Calibration procedure

1. Run the shipped reviewer against itself to establish a baseline shape:
   `srg evaluate-reviewer gemma4:e2b-it-qat`.
2. Run a reviewer known to behave badly. `granite4.1:3b` is the documented
   case; the prediction is a high directive count on clean drafts, hard false
   positives, and low distinctness. **If it does not score materially worse on
   restraint, the metrics are wrong, not the model.**
3. Read `critiques.md` end to end — 32 critiques is small enough — and check
   each deterministic verdict against your own reading. Marker matching is the
   load-bearing assumption; this is where it gets validated or fixed.
4. Only once steps 2 and 3 hold does a larger profile become worth building.
