# Security Policy

## Reporting a vulnerability

This is a part-time personal project with no security-response service-level
commitment. Reports are reviewed on a best-effort basis and may not receive a
prompt acknowledgement, investigation, fix, or release. Do not rely on this
project for time-sensitive security support.

Do not report a suspected vulnerability in a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/buggycrash/security-response-generator/security/advisories/new)
to send a description, reproduction steps, affected versions, and potential
impact to the maintainer. If private vulnerability reporting is unavailable,
email [buggycrash@yahoo.com](mailto:buggycrash@yahoo.com) instead.

Do not include real customer standards, private system context, generated
customer responses, credentials, model data, or local indexes. Build a minimal
reproduction with fictional or sanitized data whenever possible. Sending a
report does not create an embargo or coordinated-disclosure commitment.

## Supported versions

This project is currently pre-1.0. Only the current `main` branch may receive
security fixes, and no version is guaranteed ongoing security maintenance.

## Scope

Reports about SRG's code and its handling of local engagement data are in
scope. Vulnerabilities in Ollama, ChromaDB, model weights, or other third-party
components should also be reported to the component's maintainer; please
report them here when SRG's use or configuration of the component adds a
distinct risk.
