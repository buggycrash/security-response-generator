# TODO

## Next

- [ ] **Calibrate the "unwarranted changes" measurement in `srg evaluate-reviewer`
      before treating it as a ranking.** Five reviewers were run on 2026-09-05
      (see [`docs/reviewer-evaluation.md`](docs/reviewer-evaluation.md) for the
      table); `gemma4:12b-it-qat` was clearly the best reviewer yet still demanded
      ~5.5 changes per clean draft, and `granite4.1:3b` topped the headline ratio
      while being unusable. The metric currently rewards terseness, so it is a
      risk indicator rather than a score.
      Two specific questions, both answerable with the runs already on disk plus
      one or two targeted re-runs:
      1. *Is the clean corpus actually clean?* Four of five reviewers produce the
         same false positive on `si5-alert-handling/clean`, claiming the CISA
         analyst fact is absent when the draft states it verbatim. The analyst
         fact says "State SOC" while the draft expands it to "State Security
         Operations Center (SOC)"; models may be matching the literal token. Try
         a variant draft using the analyst's exact wording and see whether the
         shared false positive disappears. If it does, part of every model's
         invention score belongs to the fixture, and the clean drafts need a
         pass for paraphrase.
      2. *Establish a floor.* Have a human read every clean-draft critique and
         mark which demanded changes are genuinely unnecessary. That gives a
         measured false-positive baseline to normalise against, instead of the
         current assumption that a clean draft warrants exactly zero changes.
- [ ] **Investigate whether the reviewer prompt induces analyst-fact false
      positives.** `REVIEW_SYSTEM_INSTRUCTION`'s checklist is emphatic ("If any
      fact is missing, quote the exact missing fact ... Do not summarize this
      check with a vague, hedged judgment") and may be leading models toward
      finding a missing fact whether or not one exists. If so this is a defect in
      the **production** `--review` prompt, not a model property, and fixing it
      would improve every review pass rather than just the evaluation. Test by
      running `evaluate-reviewer` with a softened variant of that clause against
      the same corpus and comparing clean-draft invention rates. This is the most
      valuable thread the tool has surfaced so far.
- [ ] **Work out why three defect conditions are almost never detected**, before
      growing the corpus. Across the five reviewers run on 2026-09-05 (10
      chances each: 5 reviewers x 2 cases):

      | condition | detected |
      |---|---|
      | `omitted_control_clause` | 1 / 10 |
      | `unsupported_claim` | 2 / 10 |
      | `weak_validation` | 3 / 10 |
      | `wrong_customer_parameter` | 6 / 10 |
      | `narrative_validations` | 6 / 10 |
      | `wrong_control_content` | 7 / 10 |
      | `dropped_analyst_fact` | 8 / 10 |

      The bottom three are either genuinely hard for small models or badly
      markered, and those need different responses. `unsupported_claim` and
      `weak_validation` were flagged at authoring time as having the loosest
      markers, so suspect the fixture first: read those critiques by hand and
      check whether a reviewer described the defect without using the marker
      tokens. `omitted_control_clause` at 1/10 is the more interesting case,
      since its markers are omission-based and verified absent from the draft —
      if the critiques really do miss it, that is a genuine and useful finding
      about what small reviewers cannot do.
- [ ] **Grow the reviewer corpus past two cases**, once the items above are
      settled. Each defect is currently a single observation per model, so
      per-condition cells are anecdotes and the report says so. Adding cases
      before the markers are trusted would just scale up noise.

- [ ] Cap the reviewer's context window (`num_ctx`) in the `srg generate --review`
      and `bulk-generate` paths, once `srg evaluate-reviewer` has shipped and had
      a few real runs. Reviewer calls currently inherit the shared `NUM_CTX`
      (16384), which is sized for generation prompts rather than critiques: the
      largest reviewer prompt in the evaluation corpus measured ~2,900 tokens.
      The cost is memory, and a review pass is where memory is tightest, since the
      generation, reviewer, and embedding models must be resident at once —
      `phi4-mini` reserves 4.8 GB at 16384, and `embeddinggemma` was observed being
      evicted to make room for it.
      Use the reviewer evaluation's own output to choose the value rather than
      guessing: `results.json` records `prompt_eval_count` for every critique, and
      the summary's "Peak with gen + embed" column shows what a smaller window
      buys back.
      One caveat for whoever implements this: `num_ctx` alone does **not** prevent
      a runaway reviewer. A model that never emits a stop triggers Ollama's context
      shift at any window size and the request simply never returns (`phi4-mini`,
      59,000 tokens over 15m49s, fully on GPU at full speed). Bounding that needs a
      `num_predict` ceiling on the review call, which `evaluate-reviewer` already
      applies as `REVIEWER_MAX_TOKENS`. Worth doing both in one change, since
      `bulk-generate` runs unattended and is the path where a hang costs the most.
      See [`docs/reviewer-evaluation.md`](docs/reviewer-evaluation.md).

## Longer term

- [ ] Expand `srg evaluate-model` beyond its development-oriented generation
      smoke profile. Add a standard qualification suite with more fictional
      controls and repeated trials, calibrate automated rubric findings against
      blinded human judgment, and define the acceptance criteria required to
      change SRG's shipped generation-model default. Later add a separate
      reviewer-model suite that measures true- and false-positive critiques and
      whether applying them improves drafts; do not treat generation and reviewer
      qualification as the same task. See
      [`docs/model-evaluation-standard-profile.md`](docs/model-evaluation-standard-profile.md)
      for the implementation brief.
- [ ] Add a Bedrock or other cloud gateway client as an alternative to the local Ollama backend.
- [ ] Generate OSCAL-formatted output in addition to Markdown/text.
- [ ] Investigate JSON-schema-constrained decoding (`format=` on every generation/review
      call) as a major, currently-invisible cost: `srg benchmark` showed generation/review
      calls taking ~4-5x longer per output token than the model's raw decode speed
      (confirmed via direct Ollama API testing), with the overhead absent from Ollama's own
      `load`/`prompt_eval`/`eval` timing fields entirely -- it doesn't show up as slow
      token generation, it's simply unaccounted for. Likely cause is per-token grammar/
      vocabulary masking overhead inherent to constrained JSON decoding, scaling with
      output length rather than prompt size. Any fix trades off against the structured-
      output reliability this app relies on for parsing model replies, so needs a real
      design decision (schema simplification, a newer Ollama/grammar backend, shorter
      expected output, or accepting the cost) rather than a quick change.


</content>
