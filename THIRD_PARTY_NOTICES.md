# Third-Party Notices

The MIT License in [LICENSE](LICENSE) applies to the original software and
documentation in this project. It does not supersede the terms that apply to
the third-party materials and runtime components listed below.

## Models Downloaded During Setup

No model weights are included in this source repository. With the default
model configuration, setup downloads the following models into Ollama's
local model storage unless it is run with `./setup.sh --skip-models`. They
then become runtime components used by the installed project, but they are
not covered by this project's MIT License.

- **Gemma 4 E4B (QAT)** (default generation model) and **Gemma 4 E2B (QAT)**
  (default reviewer model) are distributed under the
  [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).
- **EmbeddingGemma** (default embedding model) is subject to the
  [Gemma Terms of Use](https://ai.google.dev/gemma/terms), including its
  incorporated prohibited-use policy.

Users are responsible for reviewing and complying with these terms before
downloading or using the models.

## NIST SP 800-53 Release 5.2.0

File:
[`knowledge_base/NIST.SP.800-53-oscal.md`](knowledge_base/NIST.SP.800-53-oscal.md)

The included file is an SRG-generated Markdown representation of the SP
800-53 control statements, organization-defined parameters, guidance, and
related-control references in NIST's electronic OSCAL catalog, version 5.2.0.
The SP 800-53A assessment procedures bundled in the source catalog are not
included in the generated file.

Official source:
<https://github.com/usnistgov/oscal-content/blob/v1.4.0/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json>

NIST release announcement:
<https://csrc.nist.gov/news/2025/nist-releases-revision-to-sp-800-53-controls>

NIST states that this publication is not subject to copyright in the United
States and requests attribution. NIST also publishes terms addressing reuse
outside the United States:
<https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-srd-data-software-and-technical-series-publications>

Recommended attribution:

> Reprinted courtesy of the National Institute of Standards and Technology,
> U.S. Department of Commerce. Not copyrightable in the United States.

## State of Maryland Publications

Files:

- [`example_files/MD/MD-POL-203-01-Acceptable-Use-Policy.pdf`](example_files/MD/MD-POL-203-01-Acceptable-Use-Policy.pdf)
- [`example_files/MD/MD-STD-301-AC-01-Access-Control-Standard.pdf`](example_files/MD/MD-STD-301-AC-01-Access-Control-Standard.pdf)
- [`example_files/MD/MD-STD-319-SI-01-System-and-Information-Integrity-Standard.pdf`](example_files/MD/MD-STD-319-SI-01-System-and-Information-Integrity-Standard.pdf)

Official sources:

- <https://doit.maryland.gov/policies/ci/Documents/MD-POL-203-01-Acceptable-Use-Policy.pdf>
- <https://doit.maryland.gov/policies/ci/Documents/MD-STD-301-AC-01-Access-Control-Standard.pdf>
- <https://doit.maryland.gov/policies/ci/Documents/MD-STD-319-SI-01-System-and-Information-Integrity-Standard.pdf>

Each publication states that it is approved for public distribution and may
be shared with external stakeholders, partners, and regulatory bodies. Each
also prohibits unauthorized modification or misrepresentation.

These publications are included unchanged under those stated distribution
terms, not under the MIT License. This repository grants no additional rights
to modify, misrepresent, or sublicense them. Consult the Maryland Department
of Information Technology for permissions beyond the publications' stated
terms.
