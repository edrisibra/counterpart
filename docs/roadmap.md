# Roadmap

What's intentionally **out of v0 scope**. If you're tempted to build one of these, add a
note here instead and get back to making the flagship quickstart (the lying-peer test)
flawless. "Prefer deleting to half-shipping."

## Cut from v0

- **Record/replay** — cut entirely (was in the original `agentmock` brief). It overlapped a
  well-understood mental model (vcrpy) and its removal frees scope for the contract-assertion
  engine, which is the actual differentiator. May return post-v0 if there's demand.
- **LLM-powered personas** — v0 personas are deterministic by design (reproducible in CI).
- **Production deployment / hosting / gateways / auth wiring / rate limiting / budget
  enforcement** — contested by frameworks and hyperscalers, and drags us into ops. We cover
  everything up to the moment of deploy, and nothing after.
- **Being an identity provider, registry, attestation authority, or certification body** —
  crowded land-grab (Google, Solo.io, Fortinet/IBM, IETF drafts). We test; we don't attest.
- **Runtime / production monitoring or observability dashboards.**
- **Vendor-specific mocks** (Salesforce/Snowflake/etc.).
- **Any hosted service or paid tier.**

## Threat suite — deferred personas (v0 ships 4)

v0 ships the four counterparty-native threats that map to named catalog techniques and need
no extra infrastructure: `false_success` (flagship), `prompt_injection`, `capability_lying`,
`resource_abuse`. The persona interface is designed to be extended, so these are fast-follows,
not rewrites:

- **`scope_creep`** (peer requests more context than the task needs; OWASP LLM06 / MAESTRO
  T2.2) — cheap fast-follow.
- **`auth_mishandling`** (does your agent send bearer tokens to endpoints that didn't request
  them, or talk to `http://`?; OWASP ASI07 / MAESTRO T3.3) — cheap fast-follow, but partly a
  client-config concern.
- **`unverified_signature`** (unsigned/bad-JWS Agent Card; does the client verify JWS rather
  than trusting HTTPS?) — needs a small JWS signing helper to *produce* a deliberately-bad
  signature; no clean catalog mapping exists yet.
- **`unbounded_subdelegation`** (peer recursively delegates; is depth bounded?; MAESTRO T6.3)
  — needs multi-hop delegation infrastructure; the LDP paper itself notes `max_delegation_depth`
  is tracked but not runtime-enforced, so this is the heaviest deferred item.

## A2A adapter — deferred within the binding (v0 ships the flagship path)

The A2A MockAgent v0 implements `SendMessage` (blocking + `returnImmediately`),
`SendStreamingMessage` (SSE), `GetTask`, and `CancelTask` over the JSON-RPC binding.
Deferred (return `UnsupportedOperationError`/`MethodNotFound` for now):

- **`SubscribeToTask`** (reconnect/resubscribe) — needs a per-task pub/sub to broadcast live
  events to multiple concurrent streams (spec §3.5.2). Reuses the streaming machinery.
- **Push-notification config methods** — models exist; webhook *delivery* is out of v0.
- **Card-URL-driven routing** — the client routes JSON-RPC to `{base}/` rather than honouring
  the card's declared interface URL; full routing is the conformance checker's concern.
- **gRPC and HTTP+JSON/REST bindings** — JSON-RPC only in v0 (spec-notes D1).
- **`EmitRawStatus` with an illegal/arbitrary wire status** — the directive exists, but the
  server currently coerces to a valid `TaskState`; emitting a truly illegal status string is
  the future `spec_violator` persona's job.

## Multi-protocol adapters beyond A2A

The core engine (`core/`) is protocol-agnostic on purpose (no A2A imports), so a second
adapter (e.g. an MCP-based agent protocol, or A2A's gRPC/REST bindings) is possible later
without touching the engine. v0 implements the **A2A JSON-RPC binding only** (spec-notes D1).

## Verified-solid areas (super-test, 2026-07-28)

An adversarial super-test probed dimensions the examples never touched. These held up, and are
recorded so future changes have a baseline:

- **Per-task concurrency is sound.** 200 simultaneous tasks (in-process and over a real port),
  150 concurrent SSE streams, 150 `returnImmediately` tasks hammered with concurrent `GetTask`,
  and 8 OS threads x 25 requests: unique task ids throughout, no cross-task artifact/status
  bleed, no torn reads, `received_requests` exact. Personas are genuinely per-task and
  `Contract` is stateless, so one Contract is safe to share across concurrent verifications.
- **The pydantic wire layer resists abuse.** Part-oneof violations, null/wrong-typed fields,
  envelope abuse (batch arrays, numeric `method`, wrong `jsonrpc`), 3 MB parts, 10k-element
  arrays, 10k parts, emoji/ZWJ/RTL-override/combining-char/NUL/BOM payloads: all produced
  spec-correct JSON-RPC errors or faithful round-trips — never a crash, never silent
  acceptance. `Contract.verify` never raised on hostile receipts (2000-deep dicts, 5 MB
  strings, 10k-key dicts, lone surrogates); a predicate that hits RecursionError is reported
  as a failed check, as documented.
- **The protocol-agnostic claim holds.** The Contract engine was verified end-to-end with no
  A2A involved: a plain `httpx` call to a non-A2A JSON endpoint, plain sync/async function
  returns, an MCP-shaped tool result, and `core.Lifecycle` driving a wholly invented protocol.
  `core/` was also import-checked with starlette/httpx/uvicorn blocked, confirming the guard
  test has teeth. A forgotten `await` (a coroutine passed as `result`) is caught as a
  structure failure rather than passing.

## Known limitations (surfaced by dogfooding the examples, not yet addressed)

- **Personas cannot hold state across tasks.** `MockAgent` builds a fresh `Behaviour` per task
  so concurrent sessions stay independent — the right default, but it means a persona cannot
  observe a *retry*. Modelling non-idempotency (a payer opening a second case and returning a
  different authorization number) currently needs explicit class-level state in the persona. If
  cross-task memory proves commonly necessary, add an opt-in session-scoped store rather than
  weakening per-task isolation.
- **`Contract` predicates see only the receipt.** Validating a response *against the request*
  works by closing over the request in the lambda, which reads fine, but the contract cannot
  generically report "field X did not match the request". A `.matches_request(...)` helper
  would make request/response diffs first-class.
- **`expect_status` compares raw strings.** Mixing wire values (`TASK_STATE_COMPLETED`) with
  friendly aliases (`completed`) false-positives. Core is deliberately protocol-agnostic so it
  cannot coerce A2A values; the fix is an adapter-supplied normalizer, not special-casing core.
- **Same-task operation races are unguarded.** Many tasks at once is fine (above), but two
  operations racing on the *same* task record — concurrent follow-ups, or a `CancelTask`
  overlapping an in-flight send — have no guard. Claimed by the super-test but never
  independently verified (its verifier agents were cut off mid-run), so treat as suspected.
- **A contract sees the payload, not the clock.** A peer can take 400 ms and then assert in
  its payload that the data is fresh; every rule passes. Latency/deadline enforcement belongs
  to the caller (`asyncio.timeout` around the send), because "too late to be useful" is a
  property of the business, not of the response. Demonstrated in `examples/limits_probe.py`.
  A future `.within(ms)` helper could measure the round trip and fold it into the report.
- **Cross-peer agreement is not expressible.** Ask five peers the same question and two lie:
  all five pass their own per-response contract. Quorum, median-of-N and outlier rejection are
  a layer above `Contract`, and today the user writes them. Worth considering as a first-class
  `Quorum` primitive if anyone actually fans out to redundant peers.
- **Nothing bounds delegation depth.** A self-delegating peer recurses until sockets or threads
  run out; the probe's guard is hand-written. This is the same gap the deferred
  `unbounded_subdelegation` persona would exercise.
- **`_extract_result` reads only the latest artifact**, so a task returning several artifacts
  exposes just the last one to the contract.

## Parking lot

- Agent-Card JWS signing/verification (spec §8.4) beyond the bad-signature persona stimulus.
- A2A extensions mechanics (spec §4.6) beyond declaring none.
- Legacy A2A 0.3 payload compatibility.
- Client-side conformance judging that scores the agent-under-test's *outbound* requests
  (the inverse of the a2a-tck; nothing else does this — noted as a real future differentiator).
