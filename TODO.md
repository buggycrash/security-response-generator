# TODO

## Next

- [ ] **Calibrate the atomic `srg evaluate-reviewer` profile against real models
      before expanding it.** Each call now contains only one authoritative
      requirement sentence and one mock generated sentence. The active smoke
      profile retains clean paraphrases and four high-signal defect types. The
      reviewer chooses supported, missing, contradictory, or unsupported; only
      that classification is scored. Optional constructive feedback is retained
      in `critiques.md` but never interpreted. Run the shipped reviewer against
      known weak models and confirm the overall and scenario-level accuracy
      separate them usefully. Historical runs remain qualitative evidence only;
      their metrics are not comparable with classifier suite v4.

- [ ] **Grow the reviewer corpus past two cases only after the classification
      checks discriminate reliably.** Each scenario is still one observation per
      model and case. Add a case only when it represents a materially different,
      source-provable reviewer responsibility while preserving the one-rule,
      one-statement contract.

- [ ] Cap the reviewer's context window (`num_ctx`) in the `srg generate --review`
      and `bulk-generate` paths, once `srg evaluate-reviewer` has shipped and had
      a few real runs. Reviewer calls currently inherit the shared `NUM_CTX`
      (16384), which is sized for generation prompts rather than critiques.
      Atomic reviewer-evaluation prompt sizes no longer estimate the production
      reviewer's full-response context needs. The cost is memory, and a review
      pass is where memory is tightest, since the
      generation, reviewer, and embedding models must be resident at once —
      `phi4-mini` reserves 4.8 GB at 16384, and `embeddinggemma` was observed being
      evicted to make room for it.
      Use the reviewer evaluation's own output to choose the value rather than
      guessing: `results.json` records `prompt_eval_count` for every critique, and
      the summary's coexistence result shows whether all three models actually
      remained loaded and, only when they did, their observed peak.
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
      end-to-end revision test that measures whether applying atomic classifications
      improves drafts; do not treat generation and reviewer qualification as the
      same task. See
      [`docs/model-evaluation-standard-profile.md`](docs/model-evaluation-standard-profile.md)
      for the implementation brief.
- [ ] After the atomic reviewer evaluation is calibrated, redesign production
      `--review` around the same primitive: split generated text into individual
      logical statements, pair each statement with one applicable authoritative
      requirement, and review those pairs independently. Keep statement
      decomposition, source-to-statement matching, repeated-review behavior, and
      revision integration as separate design problems rather than putting a
      full prompt and response back in front of one small reviewer model.
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
