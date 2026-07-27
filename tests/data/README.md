# Vendored A2A spec artifacts

Pinned copies of the official A2A protocol machine-readable definitions, used by the test
suite to verify `a2a_sandbox.adapters.a2a.types` against the spec instead of against our own
assumptions. Do not edit these files.

| File | What | Source (pinned) | SHA-1 |
|---|---|---|---|
| `a2a_v1.0.1.proto` | **Normative** protocol definition (spec §1.4: "the single authoritative normative definition of all protocol data objects"). proto3, `package lf.a2a.v1`. | <https://raw.githubusercontent.com/a2aproject/A2A/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto> (commit `3303592` = tag `v1.0.1`) | `2e005333992e47f9a03392f938e252ede8eaf603` |
| `a2a_v1.0.1.schema.json` | **Non-normative** generated JSON Schema bundle (produced at build time from the proto; not committed to the A2A repo — published only on the docs site). Useful as a second, independent check of our JSON serialization. | <https://a2a-protocol.org/v1.0.1/spec/a2a.json> | `f5be10ffaa9de2fcbaec7294f39be36819cda7e3` |

Retrieved 2026-07-21. Spec release: tag `v1.0.1` (published 2026-05-28); wire protocol
version `"1.0"` (Major.Minor only, per spec §3.6).

Known quirks of the generated JSON schema (see `docs/spec-notes.md` for details): it sets
`additionalProperties: false` (conflicting with §5.7's "SHOULD ignore unrecognized fields"),
accepts ProtoJSON integer enum values, and carries `patternProperties` for the snake_case
proto field names. It is used in tests only to validate JSON that *we emit*, never as the
source of truth for parsing rules.
