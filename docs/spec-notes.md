# A2A spec notes — spec v1.0 → a2a-sandbox mapping

These notes make the mapping from the A2A specification to this codebase auditable. Every
protocol fact in `a2a_sandbox/adapters/a2a/` should be traceable to a section here, and every
section here cites the spec.

**Read live on 2026-07-21 from the official sources:**

- Rendered spec: <https://a2a-protocol.org/latest/specification/> (banner: "Latest Released
  Version 1.0.0"; content verified to match tag v1.0.1)
- Spec source: <https://github.com/a2aproject/A2A>, release tag `v1.0.1`
  (commit `3303592588e388e62e0f69f701af531d2f4e3991`, published 2026-05-28)
- Normative data model: `specification/a2a.proto` at that tag (§1.4 Normative Content: "the
  file `spec/a2a.proto` is the single authoritative normative definition of all protocol
  data objects and request/response messages"). Vendored at
  [`tests/data/a2a_v1.0.1.proto`](../tests/data/a2a_v1.0.1.proto) with checksum — see
  [`tests/data/README.md`](../tests/data/README.md).
- Generated (non-normative) JSON Schema: <https://a2a-protocol.org/v1.0.1/spec/a2a.json>,
  vendored at [`tests/data/a2a_v1.0.1.schema.json`](../tests/data/a2a_v1.0.1.schema.json).

Section numbers (§) below refer to the v1.0.1 specification document.

---

## 0. Version context — v1.0 is a breaking rewrite of v0.3

The current protocol version is **`"1.0"`** (Major.Minor only; patch numbers MUST NOT be
used in negotiation, §3.6). GitHub releases: v1.0.1 (2026-05-28), v1.0.0 (2026-03-12),
v0.3.0 (2025-07-30). Much of the folklore about A2A (and this project's original brief)
describes v0.2/v0.3. What changed (§Appendix A, `whats-new-v1.md`):

| v0.3 (gone) | v1.0 (what we implement) |
|---|---|
| JSON-RPC methods `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`, `tasks/resubscribe`, `tasks/pushNotificationConfig/*`, `agent/getAuthenticatedExtendedCard` | PascalCase methods identical to gRPC: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks` (new), `CancelTask`, `SubscribeToTask`, `CreateTaskPushNotificationConfig`, `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs`, `DeleteTaskPushNotificationConfig`, `GetExtendedAgentCard` (§5.3, §9.1) |
| TaskState strings `"submitted"`, `"working"`, `"input-required"`, ... | ProtoJSON enum names `"TASK_STATE_SUBMITTED"`, `"TASK_STATE_WORKING"`, `"TASK_STATE_INPUT_REQUIRED"`, ... (§5.5) |
| `role`: `"user"` / `"agent"` | `"ROLE_USER"` / `"ROLE_AGENT"` (§4.1.5) |
| `kind` discriminators (`"message"`, `"task"`, `"status-update"`, `"artifact-update"`, part kinds `"text"`/`"file"`/`"data"`) | **Removed entirely** (§A.2.1 "Breaking Change: Kind Discriminator Removed"). The JSON member name is the discriminator (proto oneofs). |
| `TaskStatusUpdateEvent.final` flag | **Removed.** Stream end is signaled by the task reaching a terminal state + server closing the stream (§3.1.2). |
| Part union `TextPart`/`FilePart`(+`FileWithBytes`/`FileWithUri`)/`DataPart` | Single `Part` object with oneof members `text` \| `raw` \| `url` \| `data` + shared `filename`, `mediaType`, `metadata` (§4.1.6) |
| Agent Card top-level `url`, `preferredTransport`, `additionalInterfaces`, `protocolVersion`, `supportsAuthenticatedExtendedCard` | Consolidated into `supportedInterfaces[]` (`AgentInterface`) and `capabilities.extendedAgentCard` (§8.3) |
| Well-known path `/.well-known/agent.json` (0.2) → `agent-card.json` (0.3) | `/.well-known/agent-card.json` (unchanged from 0.3; §8.2, §14.3) |
| `MessageSendParams` / `configuration.blocking` / `configuration.pushNotificationConfig` | `SendMessageRequest` / `configuration.returnImmediately` / `configuration.taskPushNotificationConfig` (§3.2.1–3.2.2, Appendix A rename table) |
| JSON Schema `specification/json/a2a.json` committed in repo | proto is normative; JSON schema is a generated non-normative build artifact published on the docs site only (§1.4) |

a2a-sandbox v0 targets **spec v1.0 only**. Parsing legacy 0.3 payloads is out of scope
(roadmap note).

## 1. Serialization rules (§5.5, §5.6, §5.7)

- **camelCase everywhere**: all JSON field names are camelCase renderings of the proto
  snake_case names (`context_id` → `contextId`, `status_update` → `statusUpdate`) per
  ProtoJSON (§5.5).
  → *Code*: every model inherits `A2AModel` with `alias_generator=to_camel`,
  `populate_by_name=True`; wire I/O goes through `to_wire()` / `from_wire()`.
- **Enums serialize as proto value names** (`"TASK_STATE_INPUT_REQUIRED"`, `"ROLE_USER"`)
  (§5.5). ProtoJSON also permits integer enum values on parse; the A2A spec does not
  mandate accepting them → decision [D5](#d5).
- **Required fields** are those annotated `[(google.api.field_behavior) = REQUIRED]` in the
  proto; "Arrays marked as required MUST contain at least one element"; receivers "SHOULD
  ignore unrecognized fields" (§5.7) → decisions [D4](#d4), [D6](#d6).
- **Timestamps** (§5.6.1): ISO 8601 UTC with `Z` suffix only (offsets other than `Z` MUST
  NOT be used), pattern `YYYY-MM-DDTHH:mm:ss.sssZ`, millisecond precision SHOULD be used.
  → *Code*: `A2ATimestamp` annotated type serializes exactly that pattern.
- **bytes** (`Part.raw`): "encoded as a base64 string" (§4.1.6); the generated schema pins
  the alphabet to standard base64 with padding (`^[A-Za-z0-9+/]*={0,2}$`).
  → *Code*: `Base64Raw` emits standard padded base64; accepts unpadded input.
- **metadata** fields are `google.protobuf.Struct` → arbitrary JSON object
  (`dict[str, Any]`); `Part.data` is `google.protobuf.Value` → any JSON value.

## 2. Core data model (§4) → `a2a_sandbox/adapters/a2a/types.py`

| Model class | Proto message | Spec § | Required fields (REQUIRED in proto) |
|---|---|---|---|
| `Task` | `Task` | §4.1.1 | `id`, `status` — optional: `contextId`*, `artifacts`, `history`, `metadata` |
| `TaskStatus` | `TaskStatus` | §4.1.2 | `state` — optional: `message`, `timestamp` |
| `TaskState` (StrEnum) | `enum TaskState` | §4.1.3 | 9 values, see §3 below |
| `Message` | `Message` | §4.1.4 | `messageId`, `role`, `parts` (min 1) — optional: `contextId`, `taskId`, `metadata`, `extensions`, `referenceTaskIds` |
| `Role` (StrEnum) | `enum Role` | §4.1.5 | `ROLE_UNSPECIFIED`, `ROLE_USER` (client→server), `ROLE_AGENT` (server→client) |
| `Part` | `Part` (oneof `content`) | §4.1.6 | exactly one of `text` \| `raw` \| `url` \| `data`; shared optional `metadata`, `filename`, `mediaType` |
| `Artifact` | `Artifact` | §4.1.7 | `artifactId` (unique within task), `parts` (min 1) — optional: `name`, `description`, `metadata`, `extensions` |
| `TaskStatusUpdateEvent` | `TaskStatusUpdateEvent` | §4.2.1 | `taskId`, `contextId`, `status` — optional: `metadata`. **No `final`, no `kind`.** |
| `TaskArtifactUpdateEvent` | `TaskArtifactUpdateEvent` | §4.2.2 | `taskId`, `contextId`, `artifact` — optional: `append`, `lastChunk`, `metadata` |
| `SendMessageRequest` | `SendMessageRequest` | §3.2.1 | `message` — optional: `tenant`, `configuration`, `metadata` |
| `SendMessageConfiguration` | `SendMessageConfiguration` | §3.2.2 | all optional: `acceptedOutputModes`, `taskPushNotificationConfig`, `historyLength`, `returnImmediately` |
| `SendMessageResponse` | `SendMessageResponse` (oneof) | §3.2.3, §9.4.1 | exactly one of `task` \| `message` |
| `StreamResponse` | `StreamResponse` (oneof) | §3.2.3 | exactly one of `task` \| `message` \| `statusUpdate` \| `artifactUpdate` |
| `GetTaskRequest` | `GetTaskRequest` | §3.1.3, §9.4.3 | `id` — optional: `tenant`, `historyLength` |
| `ListTasksRequest` | `ListTasksRequest` | §3.1.4, §9.4.4 | all optional: `tenant`, `contextId`, `status`, `pageSize` (1..100, default 50), `pageToken`, `historyLength`, `statusTimestampAfter`, `includeArtifacts` (default false) |
| `ListTasksResponse` | `ListTasksResponse` | §3.1.4 | `tasks`†, `nextPageToken` (`""` on final page), `pageSize`, `totalSize` |
| `CancelTaskRequest` | `CancelTaskRequest` | §3.1.5, §9.4.5 | `id` — optional: `tenant`, `metadata` |
| `SubscribeToTaskRequest` | `SubscribeToTaskRequest` | §3.1.6, §9.4.6 | `id` — optional: `tenant` |
| `TaskPushNotificationConfig` | `TaskPushNotificationConfig` | §4.3.1‡, §10.5.1 | `url` — optional: `tenant`, `id`, `taskId`, `token`, `authentication` |
| `AuthenticationInfo` | `AuthenticationInfo` | §4.3.2 | `scheme` (IANA HTTP auth scheme) — optional: `credentials` |
| `GetTaskPushNotificationConfigRequest` | same name | §3.1.8 | `taskId`, `id` — optional: `tenant` |
| `ListTaskPushNotificationConfigsRequest` | same name | §3.1.9 | `taskId` — optional: `pageSize`, `pageToken`, `tenant` |
| `ListTaskPushNotificationConfigsResponse` | same name | §3.1.9 | optional: `configs`, `nextPageToken` |
| `DeleteTaskPushNotificationConfigRequest` | same name | §3.1.10 | `taskId`, `id` — optional: `tenant` |
| `GetExtendedAgentCardRequest` | same name | §3.1.11, §9.4.8 | optional: `tenant` (only field; §9.4.8 example omits `params` entirely) |

\* `Task.contextId` is formally optional in the proto, but §3.4.1 requires servers to
include a (possibly generated) `contextId` in responses → decision [D7](#d7).
† `ListTasksResponse.tasks` is a REQUIRED array in the proto, which per §5.7 would forbid an
empty result page — we deliberately do not enforce min-1 there (spec contradiction; see §9).
‡ §4.3.1 of the rendered site displays "Error: Message PushNotificationConfig not found." —
a site build bug; the proto message is `TaskPushNotificationConfig` (see §9).

### Agent Card model (§4.4, §4.5, §8)

| Model class | Proto message | Spec § | Required fields |
|---|---|---|---|
| `AgentCard` | `AgentCard` | §4.4.1 | `name`, `description`, `supportedInterfaces` (min 1), `version`, `capabilities`, `defaultInputModes` (min 1), `defaultOutputModes` (min 1), `skills` (min 1) — optional: `provider`, `documentationUrl`, `securitySchemes`, `securityRequirements`, `signatures`, `iconUrl` |
| `AgentInterface` | `AgentInterface` | §4.4.6 | `url`, `protocolBinding` (`"JSONRPC"` \| `"GRPC"` \| `"HTTP+JSON"` official), `protocolVersion` (e.g. `"1.0"`) — optional: `tenant` |
| `AgentProvider` | `AgentProvider` | §4.4.2 | `url`, `organization` |
| `AgentCapabilities` | `AgentCapabilities` | §4.4.3 | all optional: `streaming`, `pushNotifications`, `extensions`, `extendedAgentCard`. **No `stateTransitionHistory` in v1.0.** |
| `AgentExtension` | `AgentExtension` | §4.4.4 | all optional: `uri`, `description`, `required`, `params` |
| `AgentSkill` | `AgentSkill` | §4.4.5 | `id`, `name`, `description`, `tags` (min 1) — optional: `examples`, `inputModes`, `outputModes`, `securityRequirements` |
| `AgentCardSignature` | `AgentCardSignature` | §4.4.7 | `protected`, `signature` — optional: `header` (JWS per §8.4; verification out of v0 scope) |
| `SecurityScheme` | `SecurityScheme` (oneof) | §4.5.1 | exactly one of `apiKeySecurityScheme` \| `httpAuthSecurityScheme` \| `oauth2SecurityScheme` \| `openIdConnectSecurityScheme` \| `mtlsSecurityScheme` |
| `APIKeySecurityScheme` | same | §4.5.2 | `location` (`"query"`/`"header"`/`"cookie"` — v1.0 renamed 0.3's `in`), `name` — optional: `description` |
| `HTTPAuthSecurityScheme` | same | §4.5.3 | `scheme` — optional: `description`, `bearerFormat` |
| `OAuth2SecurityScheme` | same | §4.5.4 | `flows` — optional: `description`, `oauth2MetadataUrl` |
| `OpenIdConnectSecurityScheme` | same | §4.5.5 | `openIdConnectUrl` — optional: `description` |
| `MutualTlsSecurityScheme` | same | §4.5.6 | (only optional `description`) |
| `OAuthFlows` | `OAuthFlows` (oneof) | §4.5.7 | exactly one of `authorizationCode` \| `clientCredentials` \| `implicit` (deprecated) \| `password` (deprecated) \| `deviceCode` |
| `AuthorizationCodeOAuthFlow` | same | §4.5.8 | `authorizationUrl`, `tokenUrl`, `scopes` — optional: `refreshUrl`, `pkceRequired` |
| `ClientCredentialsOAuthFlow` | same | §4.5.9 | `tokenUrl`, `scopes` — optional: `refreshUrl` |
| `ImplicitOAuthFlow` | same | §4.5.7 (deprecated) | proto has no REQUIRED annotations: `authorizationUrl`, `refreshUrl`, `scopes` all optional |
| `PasswordOAuthFlow` | same | §4.5.7 (deprecated) | proto has no REQUIRED annotations: `tokenUrl`, `refreshUrl`, `scopes` all optional |
| `DeviceCodeOAuthFlow` | same | §4.5.10 | `deviceAuthorizationUrl`, `tokenUrl`, `scopes` — optional: `refreshUrl` |
| `SecurityRequirement` | `SecurityRequirement` | §4.4.1 | `schemes`: map of scheme name → list of scopes (proto wraps the list in `StringList`) → decision [D9](#d9) |

**Discovery**: an A2A server MUST make an Agent Card available (§8.1); the well-known URI is
exactly `https://{server_domain}/.well-known/agent-card.json` and MUST return an `AgentCard`
(§8.2, §14.3). Caching headers SHOULD be sent (`Cache-Control: max-age`, `ETag`) (§8.6).

## 3. Task lifecycle (§3.1, §3.2, §3.4, §4.1.3)

Complete `TaskState` enum (exact wire strings, §4.1.3 / proto):

| Wire value | a2a-sandbox alias | Class |
|---|---|---|
| `TASK_STATE_UNSPECIFIED` | `unspecified` | default/unknown (never emitted by our mocks) |
| `TASK_STATE_SUBMITTED` | `submitted` | active |
| `TASK_STATE_WORKING` | `working` | active |
| `TASK_STATE_COMPLETED` | `completed` | **terminal** |
| `TASK_STATE_FAILED` | `failed` | **terminal** |
| `TASK_STATE_CANCELED` | `canceled` | **terminal** |
| `TASK_STATE_INPUT_REQUIRED` | `input-required` | **interrupted** |
| `TASK_STATE_REJECTED` | `rejected` | **terminal** |
| `TASK_STATE_AUTH_REQUIRED` | `auth-required` | **interrupted** |

Terminal = `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`; interrupted = `INPUT_REQUIRED`,
`AUTH_REQUIRED` (§3.1.1/§3.1.2/§3.1.6/§3.2.2 and proto doc comments). The aliases are an
a2a-sandbox convenience (they happen to match the 0.3 wire values, which is what most humans
type); they never appear on the wire → decision [D2](#d2).

Normative lifecycle rules we implement:

- **No formal transition table exists in the spec.** Legality is implied only by
  terminal/interrupted classification and error rules → decision [D8](#d8).
- Task IDs are **server-generated**; a client-supplied `taskId` that doesn't reference an
  existing task MUST yield `TaskNotFoundError`; "Client-provided `taskId` values for
  creating new tasks is NOT supported" (§3.4.2).
- `contextId` groups tasks/messages; if the server generates one it MUST include it in the
  response; servers MUST infer `contextId` from `taskId` when only the latter is given, and
  MUST reject mismatched `contextId`+`taskId` (§3.4.1, §3.4.3).
- Messages to a task in a **terminal** state MUST fail with `UnsupportedOperationError`;
  terminal tasks cannot be restarted (§3.1.1, §3.3.3).
- **input-required flow** (§3.4.3, worked example §6.3): server returns the task with
  `status.state = "TASK_STATE_INPUT_REQUIRED"` and the agent's question in
  `status.message` (`role: "ROLE_AGENT"`); the client continues by calling `SendMessage`
  again with a *new* `Message` whose `taskId` field is the existing task's id.
- **Blocking semantics** (§3.2.2): with `returnImmediately` false/unset (default) the
  operation MUST wait until the task reaches a terminal *or interrupted* state; with `true`
  it returns immediately (client then polls `GetTask` / `SubscribeToTask`).
- `CancelTask` is idempotent; canceling a task in a terminal state → `TaskNotCancelableError`;
  duplicate cancel MAY return `TaskNotFoundError` if purged (§3.3.1, §3.1.5).
- `historyLength` (§3.2.4): unset = server default (no imposed limit); `0` = omit `history`;
  `>0` = at most N most-recent messages.
- Results SHOULD be returned as **Artifacts**, not Messages; `Task.history` is not a
  guaranteed-complete record (§3.7).
- Message deduplication by `messageId` is the server's concern (§3.3.1) — our mock server
  treats replays of the same `messageId` as idempotent.

## 4. Methods and transports (§5, §9)

v1.0 defines three bindings — JSON-RPC (§9), gRPC (§10), HTTP+JSON/REST (§11) — and **none
is mandatory**: agents MUST declare supported interfaces in the card; clients MUST select
the first supported entry (§5.2, §8.3.2). **a2a-sandbox v0 implements the JSON-RPC binding
only** (decision [D1](#d1); REST/gRPC → roadmap).

JSON-RPC binding (§9.1): JSON-RPC 2.0 over HTTP(S), `Content-Type: application/json`,
PascalCase method names identical to gRPC, streaming via SSE. The endpoint URL is whatever
the Agent Card's `supportedInterfaces[].url` says (the spec fixes no path).

Complete method set (§5.3 — also the exact JSON-RPC `method` strings):
`SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask`, `CreateTaskPushNotificationConfig`, `GetTaskPushNotificationConfig`,
`ListTaskPushNotificationConfigs`, `DeleteTaskPushNotificationConfig`,
`GetExtendedAgentCard`.

Service parameters (§3.2.6): HTTP headers `A2A-Version` and `A2A-Extensions`. Clients MUST
send `A2A-Version: 1.0` with each request (§3.6.1; MAY use a query parameter instead);
agents MUST interpret an empty/absent value as **0.3** and MUST return
`VersionNotSupportedError` for unsupported versions (§3.6.2).

### SSE wire format (§9.4.2, §9.4.6, §3.1.2, §3.1.6)

- `SendStreamingMessage` request = same params as `SendMessage`. Response: HTTP 200,
  `Content-Type: text/event-stream`; **each SSE `data:` field carries a complete JSON-RPC
  2.0 response envelope** whose `result` is a `StreamResponse`, `id` echoing the request id:
  `data: {"jsonrpc": "2.0", "id": 1, "result": {...}}` (§9.4.2). (REST binding differs: bare
  `StreamResponse` per event, §11.7 — not implemented in v0.)
- Stream patterns (§3.1.2): *Message-only* — exactly one `Message`, then close; *Task
  lifecycle* — first event is the `Task`, then zero or more status/artifact update events;
  the stream MUST close when the task reaches a terminal state.
- `SubscribeToTask` (§3.1.6): first event MUST be the full `Task` snapshot; terminal task →
  `UnsupportedOperationError`. This is the reconnection mechanism (no v0.3-style
  `tasks/resubscribe`).
- Event ordering (§3.5.2): events MUST be delivered in generation order; concurrent streams
  for one task MUST all receive the same events in the same order; closing one stream MUST
  NOT affect others.
- Capability gating (§3.3.4): `capabilities.streaming` false/absent →
  `SendStreamingMessage`/`SubscribeToTask` MUST return `UnsupportedOperationError`;
  `capabilities.pushNotifications` false/absent → all four push-config ops MUST return
  `PushNotificationNotSupportedError`.
- The spec never constrains SSE `event:`/`id:`/`retry:` fields — we emit only `data:` lines.

## 5. Errors (§3.3.2, §5.4, §9.5) → `a2a_sandbox/adapters/a2a/constants.py`

Standard JSON-RPC codes (names and default messages are spec-normative only for these five):

| Code | Name | Standard message |
|---|---|---|
| -32700 | `JSONParseError` | "Invalid JSON payload" |
| -32600 | `InvalidRequestError` | "Request payload validation error" |
| -32601 | `MethodNotFoundError` | "Method not found" |
| -32602 | `InvalidParamsError` | "Invalid parameters" |
| -32603 | `InternalError` | "Internal error" |

A2A-specific codes (range −32001..−32099; only −32001..−32009 assigned; **−32000 is not
defined**; no normative message strings — match on code, never on message text):

| Code | Name | HTTP | When |
|---|---|---|---|
| -32001 | `TaskNotFoundError` | 404 | unknown/expired/purged task id |
| -32002 | `TaskNotCancelableError` | 400 | cancel on terminal task |
| -32003 | `PushNotificationNotSupportedError` | 400 | push ops without capability |
| -32004 | `UnsupportedOperationError` | 400 | op/aspect unsupported (incl. streaming w/o capability, message to terminal task, subscribe to terminal task) |
| -32005 | `ContentTypeNotSupportedError` | 400 | unsupported media type in parts |
| -32006 | `InvalidAgentResponseError` | 500 | agent produced non-conformant response |
| -32007 | `ExtendedAgentCardNotConfiguredError` | 400 | declared but not configured |
| -32008 | `ExtensionSupportRequiredError` | 400 | required extension not declared by client |
| -32009 | `VersionNotSupportedError` | 400 | unsupported `A2A-Version` |

JSON-RPC error shape (§9.5): standard `error.code`/`error.message`, plus optional
`error.data` = **array** of objects, each carrying a `@type` key (ProtoJSON `Any`), e.g.
`{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "TASK_NOT_FOUND",
"domain": "a2a-protocol.org"}`. `reason` = error name in UPPER_SNAKE_CASE without the
`Error` suffix (§10.6/§11.6 rule).

Auth failures have **no JSON-RPC code**: the spec maps them to transport-layer HTTP
401/403 ("JSON-RPC custom error", §3.3.2).

## 6. Design decisions

<a id="d1"></a>**D1 — JSON-RPC binding only in v0.** The spec makes no binding mandatory
(§5.2). JSON-RPC + SSE is what the Python ecosystem (a2a-sdk default, fasta2a) speaks.
REST/gRPC are roadmap items; the Agent Card honestly declares only `JSONRPC`.

<a id="d2"></a>**D2 — Wire enums + friendly aliases.** `TaskState`/`Role` are `StrEnum`s
whose values are the exact wire strings (`TASK_STATE_*`, `ROLE_*`). User-facing APIs accept
friendly aliases (`"input-required"`, `"working"`, `"user"`, ...) via `TaskState.coerce()` /
`Role.coerce()` — so `task.reached_state("input-required")` works while the wire stays
spec-exact.

<a id="d3"></a>**D3 — Normative source = proto @ v1.0.1 + §5.5 ProtoJSON rules.** Field
names/required-ness come from the vendored proto. The generated JSON schema is used only as
a second check on emitted JSON (its `additionalProperties: false` and integer-enum
allowances are ignored as parsing rules, since they conflict with §5.7).

<a id="d4"></a>**D4 — `extra="ignore"` on parse.** §5.7: receivers SHOULD ignore
unrecognized fields. The conformance checker (milestone 5) may add a strict mode that
*reports* unknown fields as warnings, but models never reject them.

<a id="d5"></a>**D5 — String enum values only.** We emit and accept the ProtoJSON string
names. Integer enum values (allowed by generic ProtoJSON, not mentioned by the A2A spec) are
rejected in v0; revisit if real SDKs emit them.

<a id="d6"></a>**D6 — Required-array min-1 enforced selectively.** Enforced where clearly
intended (`Message.parts`, `Artifact.parts`, `AgentSkill.tags`, `AgentCard`'s four required
arrays). Not enforced on `ListTasksResponse.tasks` (an empty page must be representable —
spec contradiction noted in §9).

<a id="d7"></a>**D7 — `Task.contextId` optional in the model, always populated by our
server.** Parsing tolerates absence (proto-optional); the MockAgent server always sets it
(§3.4.1 server duty).

<a id="d8"></a>**D8 — Transition policy lives in the engine, not the models.** Since the
spec defines no transition table, `TaskStatus` validation does not restrict transitions.
The core engine (milestone 2) implements a documented default policy
(`submitted → working → {interrupted ↔ working} → terminal`); adversarial personas can
deliberately emit illegal transitions as a stimulus (the persona interface is designed to
allow this — a spec-violating counterparty is a roadmap fast-follow, not a v0 persona).

<a id="d9"></a>**D9 — SecurityRequirement: accept both shapes, emit strict ProtoJSON.**
Strict ProtoJSON of `SecurityRequirement{ map<string, StringList> schemes }` is
`{"schemes": {"<name>": {"list": ["scope", ...]}}}`; the §8.5 sample card instead shows
OpenAPI-style `{"<name>": ["scope", ...]}` (and calls the AgentCard field `security` while
the proto/table say `securityRequirements`). We parse **both** shapes and both field names
(`securityRequirements` preferred, `security` accepted), and emit the proto-normative form
under `securityRequirements`. The conformance checker must accept both too.

<a id="d10"></a>**D10 — Base64: emit standard padded, accept unpadded.** Per ProtoJSON and
the generated schema's pattern. URL-safe input is normalized on parse.

<a id="d11"></a>**D11 — Timestamps emit `YYYY-MM-DDTHH:mm:ss.sssZ`.** Millisecond
precision, `Z` only (§5.6.1). Parsing accepts any ISO 8601 the spec's own examples use
(e.g. second precision `2023-10-27T10:00:00Z`).

<a id="d12"></a>**D12 — Absent vs null.** Optional model fields default to `None` and are
omitted from wire output (`exclude_none`), matching proto field-presence. Known limitation:
a `Part` whose `data` member is JSON `null` (legal per `google.protobuf.Value`) cannot be
distinguished from an absent `data` — documented, revisit only if it ever matters in
practice.

<a id="d13"></a>**D13 — Stream-close on interrupted states: configurable.** §3.1.2/§3.1.6
say streams MUST close at *terminal* states; §11.7 says "terminal or interrupted". Default
behavior (milestone 3): keep the stream open on interrupted states (the stricter §3.1.2
reading); a MockAgent option will exercise the other behavior.

## 7. What the mock server MUST do (checklist for milestone 3)

- Serve a valid `AgentCard` at `/.well-known/agent-card.json` (§8.1–8.2) with
  `supportedInterfaces[0] = {url, protocolBinding: "JSONRPC", protocolVersion: "1.0"}`.
- Accept JSON-RPC 2.0 POSTs at the card's URL; reply `application/json`.
- Generate server-side task ids and contextIds; include `contextId` in responses (§3.4).
- Implement blocking `SendMessage` (wait for terminal/interrupted state) and
  `returnImmediately: true` (§3.2.2).
- SSE per §9.4.2 (JSON-RPC envelope per event); first event `Task` for task streams;
  broadcast to concurrent subscribers in order (§3.5.2); close on terminal state.
- Enforce capability gating errors (§3.3.4) and terminal-task errors (§3.1.1).
- Honor `historyLength` (§3.2.4).
- Read `A2A-Version` (empty ⇒ 0.3 semantics — we just record it; v0 serves 1.0 only and
  returns `VersionNotSupportedError` for versions ≠ 1.0 when strict mode is on).

## 8. What the client role MUST do (checklist for milestone 3)

- Fetch + validate the counterparty card from `/.well-known/agent-card.json`; select the
  first supported interface (§8.3.2).
- Send `A2A-Version: 1.0` on every request (§3.6.1).
- `SendMessage` with client-generated `messageId` (uuid), `role: "ROLE_USER"`;
  continue interrupted tasks by sending a new message with `taskId` set (§3.4.3).
- Consume both response forms (`task` | `message`) and SSE streams (JSON-RPC envelope per
  event).

## 9. Known spec bugs / internal inconsistencies (v1.0.1) and our handling

1. **`securityRequirements` vs `security`** (§4.4.1 table + proto vs §8.5 sample + §3.1.11/
   §13.3 prose) → D9: accept both, emit `securityRequirements`.
2. **SecurityRequirement wire shape** (ProtoJSON vs OpenAPI-style sample) → D9.
3. **Subscribe HTTP verb**: §5.3/§11.3.2 say `POST /tasks/{id}:subscribe`; the proto's
   `google.api.http` annotation says GET. REST is out of v0 scope; noted for the
   conformance checker (accept both).
4. **Stream close on interrupted**: §3.1.2/§3.1.6 ("terminal") vs §11.7 ("terminal or
   interrupted") → D13 (configurable, default strict).
5. **§4.3.1 renders "Error: Message PushNotificationConfig not found."** — site build bug;
   the message is `TaskPushNotificationConfig` (create op takes/returns it directly per the
   proto rpc signature).
6. **§9.3 skeleton shows `"method": "category/action"`** — stale 0.x placeholder; §9.1 and
   every concrete example use PascalCase.
7. **§6.7 file-part example is malformed JSON** (missing comma + trailing comma) — never
   copy it into fixtures; use §A.2.1's well-formed equivalents.
8. **§9.2/§11.2 examples still show `A2A-Version: 0.3`** — stale; §3.6.1's example is `1.0`.
9. **`ListTasksResponse.tasks` REQUIRED + §5.7 min-1 rule** would forbid empty pages → D6.
10. **JSON-RPC result for `DeleteTaskPushNotificationConfig` unspecified** (gRPC returns
    `Empty`). We will emit `"result": null` and accept `null`/`{}`.

## 10. Verified vs assumed

**Verified against the live spec/proto (every claim adversarially re-checked by a second
independent pass; 135/136 claims confirmed, 0 refuted):** everything in sections 0–5 and 9
above — enum values, method names, error codes, field names/required-ness, SSE framing,
lifecycle rules, discovery path, versioning header semantics.

**Assumed / our own judgment (not spec-mandated):** the friendly state aliases (D2); the
default transition policy (D8); emitting strict-ProtoJSON `SecurityRequirement` (D9 — the
spec is self-contradictory); rejecting integer enums (D5); `result: null` for delete (§9.10);
treating `Section 6` examples as non-normative when they conflict with the proto (§1.4 says
the proto wins).

**Deliberately out of v0 scope** (see `docs/roadmap.md`): gRPC and REST bindings, extended
agent card serving, push-notification *delivery* (models exist; webhook sending/receiving is
roadmap), agent-card JWS signing/verification (§8.4), extensions mechanics (§4.6) beyond
declaring none, legacy 0.3 payload compatibility, multi-tenant routing (`tenant` fields are
modeled and echoed, not routed).
