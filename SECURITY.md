# Security Policy

## Supported versions

This is a pre-release research prototype. Security fixes are applied only to the latest revision of the default branch; no stable supported release exists yet.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability involving credentials, participant identifiers, health data, remote execution, path traversal, dependency compromise, or unsafe actuation.

Before public release, enable GitHub **Private vulnerability reporting** under repository security settings. Use that private channel for reports. If the feature is not enabled, contact the repository owner through a private institutional channel established for the project.

Include:

- affected version or commit;
- reproduction steps using synthetic data only;
- expected and observed behavior;
- potential confidentiality, integrity, availability, or safety impact;
- suggested mitigation, if available.

Do not include real participant records, passwords, API keys, access tokens, or production endpoints in a report.

## Security boundaries

The current repository does not provide production controls for:

- encryption at rest or in transit;
- authentication or authorization;
- tenant or participant isolation;
- audit logging and tamper evidence;
- secret management;
- retention, deletion, backup, or disaster recovery;
- dependency provenance or signed releases;
- network or device hardening;
- real-time watchdog and fail-safe hardware behavior.

`SessionRecorder` writes pseudonymous identifiers and physiology-derived records to plain JSON. Use only synthetic data unless an approved protected storage layer is substituted.

`MubertEngine` is not implemented. Never place a vendor API key directly in source, configuration committed to Git, logs, or issue reports.

## Safety-related defects

Failures involving missed safety escalation, unintended actuation, incorrect research-arm behavior, corrupted dose counts, or cross-participant records should be handled as high-priority security/safety reports even when they are not conventional cybersecurity vulnerabilities.

## Disclosure

The maintainer should acknowledge a private report, reproduce it with synthetic data, coordinate a fix and tests, and agree on a disclosure timeline before publishing details. No response-time guarantee is offered until a staffed maintenance and security process is established.
