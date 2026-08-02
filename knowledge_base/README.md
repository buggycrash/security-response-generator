# knowledge_base/

Drop universal, public, engagement-independent reference material here — most
importantly the generated **NIST SP 800-53** control catalog. This tier is
committed to git since it is public and does not change between customer
engagements.

Supported formats: `.pdf`, `.md`, `.txt`.

To download and convert the supported official OSCAL catalog:

```bash
srg update-nist
```

The generated `NIST.SP.800-53-oscal.md` records its exact source, catalog
version, and source SHA-256 digest. A future OSCAL release or local JSON file
can be selected with `srg update-nist --source <HTTPS-URL-or-path>`.

After adding or changing files here, run:

```bash
srg ingest --source knowledge_base
```

`srg generate <control-id>` treats this as the baseline source of truth for
what a control *is*. If a control ID has no match anywhere in this folder,
`srg generate` refuses to answer rather than let the model guess.
