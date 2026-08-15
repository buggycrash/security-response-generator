# TODO

- [ ] Design review

## Longer term

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
