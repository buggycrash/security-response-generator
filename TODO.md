# TODO



## Longer term

- [ ] Add a model-evaluation feature for generated responses, inspired by the
      "Evaluating research agents" approach in
      https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
      (multiple graders rather than one score: groundedness against retrieved
      chunks, coverage of required control elements, source-tier quality
      customer/state standard vs. generic NIST vs. private context, plus an
      open-ended LLM rubric for coherence/completeness -- and a reminder that
      LLM-based rubric grading should be periodically calibrated against
      human/assessor judgment, not trusted blindly). Reuse the existing
      `--review` critique pipeline (`generation/review.py`,
      `_review_and_revise()` in `cli.py`) as the starting point, but change
      the output behavior: today the reviewer's critique is purely internal
      and is folded straight into a revision prompt, never shown to the user
      (see `parse_critique()` / `revision_instruction()` in
      `generation/review.py`). This feature should instead print the
      critique/eval findings to the screen for the human to read, likely as
      a new opt-in mode (e.g. `srg generate --evaluate`) separate from the
      existing silent revise-only `--review` flag.
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
