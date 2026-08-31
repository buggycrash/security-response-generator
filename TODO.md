# TODO



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
