# knowledge_base/

The  **NIST SP 800-53** control catalog is the authoritative knowledge base. Version 5.2.0 is included with this tool.

If NIST releases a newer revision, perform:

```bash
srg update-nist --source <HTTPS-URL>
```

This will download the official OSCAL version directly from NIST and convert it to a format SRG can use.

The generated `NIST.SP.800-53-oscal.md` file records its exact source, catalog
version, and source SHA-256 digest.

After updating the knowledge base, run:

```bash
srg ingest --source knowledge_base
```
