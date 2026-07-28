# Security policy

## Supported versions

counterpart is at v0. Only the latest release receives fixes; there are no maintained
branches for older versions yet.

## Reporting a vulnerability

Please report security issues **privately**, via GitHub's [private vulnerability
reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, not in a public issue.

Include the version, a reproduction, and what an attacker gains. You can expect an
acknowledgement within a few days; this is a small project, so please be patient with fixes.

## What is in scope

This is a **testing library**, so its threat model is unusual and worth stating plainly.

**In scope**, please report these:

- The library reading a payload from a counterparty and executing, deserialising, or otherwise
  trusting it in a way that lets a malicious counterparty compromise the *test runner* (code
  execution, path traversal, resource exhaustion beyond what a test opted into).
- A `Contract` rule that **fails open**, reporting `satisfied` for a result that does not meet
  the declared requirements. This is the most serious class of bug in this project: a user
  relies on it to reject bad delegated work, so a silent pass is a real failure of the tool's
  one job.
- `MockAgent.serve()` binding more broadly than intended, or leaking beyond the intended host.
- Credential or PHI/PII leakage in logs, reports, or the recorded request log.

**Out of scope**, these are the product working as designed:

- **The adversarial personas emit hostile content on purpose.** Prompt-injection strings,
  malformed payloads, and spec-violating responses are the *feature*. A persona producing
  nasty output is not a vulnerability in this library.
- Reports that the library lets you construct invalid A2A traffic. It is explicitly designed
  to do that; a compliant SDK cannot, which is why this exists.
- Findings against an agent *you* tested with this tool. Those belong to that agent's
  maintainers, not here.

## A word on running the personas

The adversarial personas are designed to be pointed at **your own agents, in test
environments**. Pointing them at a third party's production endpoint without authorisation may
be unlawful, and is not a use this project endorses or supports.
