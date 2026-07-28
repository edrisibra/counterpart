# Prior art, what exists, and what counterpart deliberately does differently

Researched live on 2026-07-21 and 2026-07-27 (GitHub, PyPI, npm, project docs).

## The gap, stated precisely (corrected 2026-07-27)

An earlier draft claimed "**nothing** provides behavioral simulated counterparty agents."
Follow-up research narrowed that: it is **not** true in general. What is true is more
specific, and it's the niche counterpart owns:

- **The role.** Existing A2A mocks (aimock, mokksy) play the counterparty *responder* role
  but ship **zero adversarial behavior**, they're honest, mostly stateless stubs.
- **The adversary.** `agent-security-harness` (PyPI, Apache-2.0) *does* send adversarial A2A
  wire-protocol traffic (13 named tests: card spoofing, task injection, push-URL redirect,
  cross-context leakage). **But** it is a *client attacker pointed at your agent's server
  ingress* (`--url your-agent`), it does not act as a malicious counterparty that your agent
  *delegates to* and that speaks adversarial responses back.
- **The open niche = responder-role × adversarial × stateful.** No tool combines all three:
  a stateful, persona-driven counterparty that your agent calls, which can cooperate, stall,
  break mid-task, or lie in its responses, paired with contract assertions that catch a peer
  reporting success while returning garbage. That is counterpart.

Positioning follows from this (decision, 2026-07-27): we describe `agent-security-harness` as
**complementary**, it hardens your A2A *ingress*; counterpart hardens your *egress / delegation
trust*, and we do **not** market ourselves as "the only adversarial A2A tool." We also drop
the "you can't test A2A without infrastructure" hook, whose own cited source argues the
opposite (untestability is an avoidable architecture smell); we instead promise to *provide the
protocol boundary* so you can test delegation trust in isolation.

Everything below helps you **build, serve, or debug your own** A2A agent, stubs honest
request/response pairs, or attacks your *ingress*, none is a stateful adversarial counterparty
with contract verification.

## Official A2A ecosystem

### a2a-sdk (a2aproject/a2a-python), Apache-2.0

The official Python SDK, v1.1.1 (2026-07-16), implements spec v1.0 with a 0.3 compat layer.
Provides protobuf-generated types (`a2a.types`), a client (transports: JSON-RPC, REST,
gRPC), and a server framework (AgentExecutor, TaskStore, EventQueue, Starlette/FastAPI
routes).

- **They**: reference implementation for building production agents; its design goal is
  conformance, it actively makes it hard to send invalid traffic.
- **We**: need to *produce* invalid, hostile, and degenerate traffic on purpose
  (the `false_success`, `prompt_injection`, `capability_lying`, `resource_abuse` personas),
  which requires a raw wire layer the SDK cannot express. We also ship the testing ergonomics
  it has none of: personas, fault injection, contract assertions over the returned result,
  pytest fixtures, ephemeral server lifecycle.
- **Deliberate choice**: counterpart does **not** depend on a2a-sdk. Our own Pydantic models
  (verified against the same normative proto, see `docs/spec-notes.md`) keep the dependency
  surface small and let us emit deliberately-wrong payloads. We may add an optional
  interop test against the SDK later (roadmap).

### fasta2a (Pydantic team, repo now under Datalayer), MIT

"Convert an AI Agent into an A2A server": Starlette/ASGI server plumbing
(Storage/Broker/Worker abstractions), v0.6.1 (2026-05-15), tracks A2A v1. Also has a minimal
httpx client used by its own tests.

- **They**: BUILD and SERVE real agents; you bring the agent logic. No mock counterparties,
  no personas, no fault injection, no pytest fixtures; the docs never mention testing your
  agent against a remote counterparty.
- **We**: the counterparty side. A compliant, validated pipeline like fasta2a structurally
  cannot emit malformed JSON-RPC or illegal state transitions, our adversarial personas
  need to.
- Its Storage/Broker/Worker seam and in-process ASGI test patterns are good reference
  material; its maintenance transfer (Pydantic → Datalayer) reinforced our choice not to
  depend on it.

### a2a-inspector (a2aproject), Apache-2.0

Web GUI (FastAPI + TS frontend) to connect to a live agent, view its card, run basic
field-validation checks, chat with it, and watch raw JSON-RPC traffic. Human-in-the-loop
debugging; v0.1.0, quiet since 2026-02.

- **They**: interactive inspection of an agent you point it at; not embeddable in CI, no
  simulation.
- **We**: programmatic, CI-first; our mock servers keep a machine-readable log of received
  traffic (the same observability, but assertable).

### a2a-tck (a2aproject), Apache-2.0 (LICENSE file; pyproject says MIT, upstream discrepancy)

The official Technology Compatibility Kit: a pytest-driven conformance suite you run
*against your own server* (`./run_tck.py --sut-host URL`), spec-pinned (vendors a2a.json /
a2a.proto / specification.md + version.json), RFC 2119-leveled (MUST=fail, SHOULD=xfail,
MAY=skip), covering agent card, task lifecycle, artifacts, errors, push configs, streaming,
across all three bindings, with compatibility.json/HTML reports. Actively maintained; not on
PyPI (clone-and-run).

- **They**: the definitive *server-side* conformance suite. We must NOT re-implement it.
- **We / how `counterpart check` differs** (matters for milestone 5):
  1. **Scope**: `counterpart check` is a fast, pip-installed smoke report (scored table +
     JSON, spec citations), a developer loop tool, not a certification suite. Its docs will
     point to the TCK for the full matrix.
  2. **Direction**: the TCK has no mode that judges a *client's* outbound traffic. Our mock
     servers can score the agent-under-test's requests (headers, ids, follow-up semantics), 
     the inverse feature, which nothing else offers.
  3. We copy their good ideas: spec pinning with checksums (already done in `tests/data/`),
     RFC 2119 leveling, machine-readable report. And we can use the TCK itself as a CI
     oracle against our own `cooperative` persona server, the TCK becomes counterpart's
     fidelity gate rather than a competitor.

## Non-official / adjacent tools

### agent-security-harness (PyPI), Apache-2.0, *the nearest adversarial tool*

Self-described "active protocol exploitation + wire-protocol adversarial testing." Ships 13
named A2A adversarial tests (A2A-001..013): Agent Card discovery/integrity and **spoofing via
message metadata** (P0), card path traversal, unauthorized task access/cancel, **task message
injection** (P0), task-state manipulation, **push-notification URL redirect** (P0),
unauthorized skill request, artifact content-type abuse, malformed-request handling,
undocumented-method enumeration, **cross-context data leakage** (P0).

- **They**: a *client attacker* aimed at your agent's A2A **server ingress**
  (`--url https://agent.example.com`). It probes whether your agent's endpoint is exploitable.
- **We**: the *counterparty responder* your agent **delegates to**. It never plays the
  remote-agent role or speaks adversarial *responses* back to a delegating client; it has no
  personas, no stateful multi-turn behavior, and no contract verification of returned work.
- **Relationship**: complementary, not competing, it hardens ingress, counterpart hardens
  egress / delegation trust. We cite it that way and do not claim to be the only adversarial
  A2A tool. (See "The gap, stated precisely" above.)

### CopilotKit aimock (@copilotkit/aimock, npm), MIT, *closest cooperative-mock prior art*

TypeScript "mock everything your AI app talks to" server (12 LLM APIs, MCP, A2A, vector
DBs). Its A2A module serves an agent card, SendMessage with pattern→canned-response routing,
SSE streaming with per-event delays, GetTask/ListTasks/CancelTask; sends `A2A-Version: 1.0`.
Suite-wide chaos testing (500s, malformed JSON, mid-stream disconnects) and "streaming
physics" (TTFT/tokens-per-sec/jitter), but per its own docs those are wired to the LLM
mocks, not the A2A surface.

- **They**: npm-only, stateless fixture matching (pattern → canned task), no multi-turn
  state machine, no personas, no A2A-specific fault injection, no Python/pytest story.
- **We**: Python-native, pytest-first, *stateful behavioral* counterparties (a clarifier
  that actually walks `submitted → input-required → completed` across turns), adversarial
  personas (prompt injection, spec violations) that aimock doesn't attempt.
- Worth borrowing: chaos-probability knobs and streaming-physics vocabulary (TTFT / tokens-
  per-sec / jitter) for the `resource_abuse` persona's stall/slow-stream timing options.

### mokksy / ai-mocks-a2a (Kotlin/JVM), Apache-2.0

WireMock-style `MockAgentServer` with the fullest A2A endpoint coverage of any stub tool
(11 endpoints incl. push-config CRUD), true SSE, per-response delays. Pinned to **spec
0.3.0**, JSON-RPC only, JVM only, pure request/response stubbing.
- Useful as an endpoint-coverage checklist; being current on spec 1.0 is a differentiator.

### inference-gateway/mock-agent (Go), Apache-2.0

A deployable Dockerized mock A2A agent (env-var/agent.yaml configured) with a fake LLM
backend, delay/error-injection knobs. A binary you run, not a test library; no pytest
integration, no personas, no assertions.

### Generic analogies (pattern, not overlap)

LocalStack (simulated AWS), WireMock (programmable HTTP stubs), respx / pytest-httpx
(in-process httpx mocking), vcrpy (record/replay cassettes). All fake *services with
deterministic request/response semantics*. None model an interactive, stateful,
conversational counterparty that can cooperate, stall, break mid-task, or attack you, 
that's the specific thing counterpart adds, for the A2A protocol.

## Scope guardrails derived from this research

1. Do not re-implement the TCK's server-conformance matrix; `counterpart check` stays a
   scored smoke report + client-side judging, and links to the TCK.
2. Do not mock LLM APIs, MCP, or vector DBs, aimock owns that ground.
3. Do not depend on a2a-sdk or fasta2a; verify our own models against the pinned proto.
4. PyPI naming: `counterpart` / `counterpart` are both **available** on PyPI (checked
   2026-07-27), as is `agentmock`; `a2a-inspector` is taken by an unrelated placeholder wheel.
