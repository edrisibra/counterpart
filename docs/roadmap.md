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
- **`_extract_result` reads only the latest artifact**, so a task returning several artifacts
  exposes just the last one to the contract.

## Parking lot

- Agent-Card JWS signing/verification (spec §8.4) beyond the bad-signature persona stimulus.
- A2A extensions mechanics (spec §4.6) beyond declaring none.
- Legacy A2A 0.3 payload compatibility.
- Client-side conformance judging that scores the agent-under-test's *outbound* requests
  (the inverse of the a2a-tck; nothing else does this — noted as a real future differentiator).
