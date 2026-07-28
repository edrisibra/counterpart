"""Pydantic v2 models for the A2A protocol data model, spec v1.0 (tag v1.0.1).

The normative source is ``specification/a2a.proto`` (vendored with checksum at
``tests/data/a2a_v1.0.1.proto``) plus the ProtoJSON serialization rules of spec
section 5.5. The mapping and all design decisions (D1-D13) are documented in
``docs/spec-notes.md``. Every class below cites its proto message and spec section.

Wire conventions implemented here:

- JSON field names are camelCase (section 5.5): models declare snake_case Python
  fields with a camelCase alias generator; use ``to_wire()`` and ``from_wire()``.
- Enums serialize as their proto value names, e.g. ``"TASK_STATE_WORKING"`` (D5).
- Unrecognized fields are ignored on parse (section 5.7, D4).
- Optional fields are ``None`` when absent and omitted from wire output (D12).
- proto ``oneof`` groups ("exactly one of") are enforced by model validators.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Final, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

# ---------------------------------------------------------------------------
# Scalar wire types
# ---------------------------------------------------------------------------


def _serialize_timestamp(value: datetime) -> str:
    """Spec section 5.6.1: ISO 8601 UTC, 'Z' suffix only, millisecond precision."""
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S") + f".{value.microsecond // 1000:03d}Z"


# Parsing accepts general ISO 8601 (the spec's own examples use second precision);
# output is always the section 5.6.1 pattern YYYY-MM-DDTHH:mm:ss.sssZ (D11).
A2ATimestamp = Annotated[
    datetime, PlainSerializer(_serialize_timestamp, return_type=str, when_used="json")
]


def _decode_base64(value: Any) -> Any:
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    if isinstance(value, str):
        normalized = value.replace("-", "+").replace("_", "/")
        normalized += "=" * (-len(normalized) % 4)
        try:
            return base64.b64decode(normalized, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"invalid base64 content: {exc}") from exc
    return value


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


# Part.raw: "In JSON serialization, this is encoded as a base64 string" (section 4.1.6).
# Decode base64 on the way in; emit standard padded base64 out. Accept unpadded and
# URL-safe input (D10). ``bytes`` input passes through unchanged.
Base64Raw = Annotated[
    bytes,
    BeforeValidator(_decode_base64),
    PlainSerializer(_encode_base64, return_type=str, when_used="json"),
    Field(json_schema_extra={"format": "byte"}),
]

Metadata = dict[str, Any]  # google.protobuf.Struct: an arbitrary JSON object


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class A2AModel(BaseModel):
    """Base for all A2A wire objects: camelCase aliases, lenient to unknown fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    def to_wire(self) -> dict[str, Any]:
        """Spec-exact JSON-compatible dict: camelCase keys, absent fields omitted."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def to_wire_json(self) -> str:
        return json.dumps(self.to_wire())

    @classmethod
    def from_wire(cls, data: Any) -> Self:
        """Parse a wire payload (dict, JSON string, or JSON bytes)."""
        if isinstance(data, str | bytes | bytearray):
            return cls.model_validate_json(data)
        return cls.model_validate(data)


def _exactly_one_of(model: A2AModel, group: tuple[str, ...]) -> None:
    """Enforce proto ``oneof`` semantics: exactly one member of *group* is set."""
    present = [name for name in group if getattr(model, name) is not None]
    if len(present) != 1:
        aliases = ", ".join(to_camel(name) for name in group)
        found = ", ".join(to_camel(name) for name in present) or "none"
        raise ValueError(
            f"{type(model).__name__} must contain exactly one of: {aliases} (found: {found})"
        )


# ---------------------------------------------------------------------------
# Enums (spec sections 4.1.3, 4.1.5)
# ---------------------------------------------------------------------------

_TASK_STATE_PREFIX: Final = "TASK_STATE_"


class TaskState(StrEnum):
    """proto ``enum TaskState`` (section 4.1.3). Values are the exact wire strings."""

    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"

    @property
    def alias(self) -> str:
        """Human-friendly counterpart alias, e.g. ``"input-required"`` (D2, never on wire)."""
        return self.value.removeprefix(_TASK_STATE_PREFIX).lower().replace("_", "-")

    @property
    def is_terminal(self) -> bool:
        """Terminal states per sections 3.1.1/3.1.2/3.1.6: no further messages accepted."""
        return self in _TERMINAL_STATES

    @property
    def is_interrupted(self) -> bool:
        """Interrupted states per section 3.2.2: task paused awaiting input or auth."""
        return self in _INTERRUPTED_STATES

    @classmethod
    def coerce(cls, value: TaskState | str) -> TaskState:
        """Accept a TaskState, a wire string, or a friendly alias like ``"input-required"``."""
        if isinstance(value, TaskState):
            return value
        known = ", ".join(sorted(s.alias for s in cls))
        if not isinstance(value, str):
            raise ValueError(
                f"unknown task state {value!r}; expected a wire value or one of: {known}"
            )
        try:
            return cls(value)
        except ValueError:
            pass
        normalized = value.strip().lower().replace("_", "-")
        for state in cls:
            if state.alias == normalized:
                return state
        raise ValueError(f"unknown task state {value!r}; expected a wire value or one of: {known}")


_TERMINAL_STATES: Final = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}
)
_INTERRUPTED_STATES: Final = frozenset({TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED})


class Role(StrEnum):
    """proto ``enum Role`` (section 4.1.5). USER = client->server, AGENT = server->client."""

    UNSPECIFIED = "ROLE_UNSPECIFIED"
    USER = "ROLE_USER"
    AGENT = "ROLE_AGENT"

    @property
    def alias(self) -> str:
        return self.value.removeprefix("ROLE_").lower()

    @classmethod
    def coerce(cls, value: Role | str) -> Role:
        if isinstance(value, Role):
            return value
        if not isinstance(value, str):
            raise ValueError(f"unknown role {value!r}")
        try:
            return cls(value)
        except ValueError:
            pass
        normalized = value.strip().lower()
        for role in cls:
            if role.alias == normalized:
                return role
        raise ValueError(f"unknown role {value!r}")


# ---------------------------------------------------------------------------
# Core objects (spec section 4.1)
# ---------------------------------------------------------------------------


class Part(A2AModel):
    """proto ``Part`` (section 4.1.6): oneof content = text | raw | url | data.

    There is no ``kind`` discriminator in v1.0. The member name is the discriminator
    (Appendix A.2.1). ``filename``, ``mediaType``, ``metadata`` are shared by all
    variants. Note (D12): a ``data`` member holding JSON ``null`` is treated as absent.
    """

    _ONEOF: ClassVar[tuple[str, ...]] = ("text", "raw", "url", "data")

    text: str | None = None
    raw: Base64Raw | None = None
    url: str | None = None
    data: Any = None
    metadata: Metadata | None = None
    filename: str | None = None
    media_type: str | None = None

    @model_validator(mode="after")
    def _check_oneof(self) -> Self:
        _exactly_one_of(self, self._ONEOF)
        return self


class Message(A2AModel):
    """proto ``Message`` (section 4.1.4). ``messageId`` is created by the message creator.

    Section 3.4.3: a client continues an interrupted task by sending a new Message
    with the existing task's id in ``taskId``.
    """

    message_id: str
    role: Role
    parts: list[Part] = Field(min_length=1)  # REQUIRED array => at least one (section 5.7)
    context_id: str | None = None
    task_id: str | None = None
    metadata: Metadata | None = None
    extensions: list[str] | None = None
    reference_task_ids: list[str] | None = None


class Artifact(A2AModel):
    """proto ``Artifact`` (section 4.1.7). Task outputs SHOULD be artifacts (section 3.7)."""

    artifact_id: str  # unique within a task
    parts: list[Part] = Field(min_length=1)  # "Must contain at least one part."
    name: str | None = None
    description: str | None = None
    metadata: Metadata | None = None
    extensions: list[str] | None = None


class TaskStatus(A2AModel):
    """proto ``TaskStatus`` (section 4.1.2).

    In the input-required flow the agent's question travels in ``message``
    (role ROLE_AGENT) alongside ``state = TASK_STATE_INPUT_REQUIRED`` (section 6.3).
    """

    state: TaskState
    message: Message | None = None
    timestamp: A2ATimestamp | None = None


class Task(A2AModel):
    """proto ``Task`` (section 4.1.1). ``id`` is always server-generated (section 3.4.2).

    ``contextId`` is proto-optional, but servers must include a generated one in
    responses (section 3.4.1); counterpart's server always populates it (D7).
    """

    id: str
    status: TaskStatus
    context_id: str | None = None
    artifacts: list[Artifact] | None = None
    history: list[Message] | None = None
    metadata: Metadata | None = None


# ---------------------------------------------------------------------------
# Streaming events (spec section 4.2)
# ---------------------------------------------------------------------------


class TaskStatusUpdateEvent(A2AModel):
    """proto ``TaskStatusUpdateEvent`` (section 4.2.1). No ``final`` flag in v1.0.
    Stream end is signaled by a terminal state plus stream close (section 3.1.2)."""

    task_id: str
    context_id: str
    status: TaskStatus
    metadata: Metadata | None = None


class TaskArtifactUpdateEvent(A2AModel):
    """proto ``TaskArtifactUpdateEvent`` (section 4.2.2). ``append``: add to previously
    sent artifact with the same id; ``lastChunk``: final chunk of the artifact."""

    task_id: str
    context_id: str
    artifact: Artifact
    append: bool | None = None
    last_chunk: bool | None = None
    metadata: Metadata | None = None


class StreamResponse(A2AModel):
    """proto ``StreamResponse`` (section 3.2.3): oneof task | message | statusUpdate |
    artifactUpdate. Also the push-notification webhook payload (section 4.3.3)."""

    _ONEOF: ClassVar[tuple[str, ...]] = ("task", "message", "status_update", "artifact_update")

    task: Task | None = None
    message: Message | None = None
    status_update: TaskStatusUpdateEvent | None = None
    artifact_update: TaskArtifactUpdateEvent | None = None

    @model_validator(mode="after")
    def _check_oneof(self) -> Self:
        _exactly_one_of(self, self._ONEOF)
        return self

    @property
    def payload(self) -> Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent:
        for name in self._ONEOF:
            value = getattr(self, name)
            if value is not None:
                return value  # type: ignore[no-any-return]
        raise AssertionError("unreachable: oneof validated at construction")


# ---------------------------------------------------------------------------
# Operation requests and responses (spec sections 3.1, 3.2)
# ---------------------------------------------------------------------------


class AuthenticationInfo(A2AModel):
    """proto ``AuthenticationInfo`` (section 4.3.2). ``scheme`` is an IANA HTTP auth
    scheme name, e.g. ``Bearer``."""

    scheme: str
    credentials: str | None = None


class TaskPushNotificationConfig(A2AModel):
    """proto ``TaskPushNotificationConfig`` (sections 4.3.1, 10.5.1). This one message is
    both the Create request and the Create or Get response (there is no separate
    ``PushNotificationConfig`` message in v1.0: the rendered section 4.3.1 table is a
    site build bug; see docs/spec-notes.md section 9)."""

    url: str
    tenant: str | None = None
    id: str | None = None
    task_id: str | None = None
    token: str | None = None
    authentication: AuthenticationInfo | None = None


class SendMessageConfiguration(A2AModel):
    """proto ``SendMessageConfiguration`` (section 3.2.2). All fields optional.

    ``returnImmediately`` false or unset = blocking (wait for terminal or interrupted
    state). v0.3's ``blocking`` and ``pushNotificationConfig`` fields do not exist in v1.0.
    """

    accepted_output_modes: list[str] | None = None
    task_push_notification_config: TaskPushNotificationConfig | None = None
    history_length: int | None = Field(default=None, ge=0)
    return_immediately: bool | None = None


class SendMessageRequest(A2AModel):
    """proto ``SendMessageRequest`` (section 3.2.1), params of SendMessage and
    SendStreamingMessage (section 9.4.1/9.4.2)."""

    message: Message
    tenant: str | None = None
    configuration: SendMessageConfiguration | None = None
    metadata: Metadata | None = None


class SendMessageResponse(A2AModel):
    """proto ``SendMessageResponse`` (section 3.2.3): oneof task | message."""

    _ONEOF: ClassVar[tuple[str, ...]] = ("task", "message")

    task: Task | None = None
    message: Message | None = None

    @model_validator(mode="after")
    def _check_oneof(self) -> Self:
        _exactly_one_of(self, self._ONEOF)
        return self

    @property
    def payload(self) -> Task | Message:
        result = self.task if self.task is not None else self.message
        if result is None:  # pragma: no cover - oneof validated at construction
            raise AssertionError("unreachable")
        return result


class GetTaskRequest(A2AModel):
    """proto ``GetTaskRequest`` (sections 3.1.3, 9.4.3). ``historyLength`` semantics
    (section 3.2.4): unset = server default, 0 = omit history, >0 = at most N."""

    id: str
    tenant: str | None = None
    history_length: int | None = Field(default=None, ge=0)


class ListTasksRequest(A2AModel):
    """proto ``ListTasksRequest`` (sections 3.1.4, 9.4.4). New in v1.0."""

    tenant: str | None = None
    context_id: str | None = None
    status: TaskState | None = None
    page_size: int | None = Field(default=None, ge=1, le=100)  # default 50, min 1, max 100
    page_token: str | None = None
    history_length: int | None = Field(default=None, ge=0)
    status_timestamp_after: A2ATimestamp | None = None
    include_artifacts: bool | None = None  # default false => artifacts MUST be omitted


class ListTasksResponse(A2AModel):
    """proto ``ListTasksResponse`` (section 3.1.4). All four fields REQUIRED; ``tasks``
    may still be empty (spec contradiction, D6). ``nextPageToken`` MUST be ``""`` on
    the final page; tasks sorted by status timestamp descending."""

    tasks: list[Task]
    next_page_token: str
    page_size: int
    total_size: int


class CancelTaskRequest(A2AModel):
    """proto ``CancelTaskRequest`` (sections 3.1.5, 9.4.5). Returns the updated Task."""

    id: str
    tenant: str | None = None
    metadata: Metadata | None = None


class SubscribeToTaskRequest(A2AModel):
    """proto ``SubscribeToTaskRequest`` (sections 3.1.6, 9.4.6). First stream event MUST
    be the full Task snapshot; terminal task => UnsupportedOperationError."""

    id: str
    tenant: str | None = None


class GetTaskPushNotificationConfigRequest(A2AModel):
    """proto message of the same name (section 3.1.8)."""

    task_id: str
    id: str
    tenant: str | None = None


class ListTaskPushNotificationConfigsRequest(A2AModel):
    """proto message of the same name (section 3.1.9)."""

    task_id: str
    page_size: int | None = Field(default=None, ge=1)
    page_token: str | None = None
    tenant: str | None = None


class ListTaskPushNotificationConfigsResponse(A2AModel):
    """proto message of the same name (section 3.1.9)."""

    configs: list[TaskPushNotificationConfig] | None = None
    next_page_token: str | None = None


class DeleteTaskPushNotificationConfigRequest(A2AModel):
    """proto message of the same name (section 3.1.10). Deletion MUST be idempotent;
    the JSON-RPC result is unspecified, so counterpart emits ``result: null``."""

    task_id: str
    id: str
    tenant: str | None = None


class GetExtendedAgentCardRequest(A2AModel):
    """proto message of the same name (sections 3.1.11, 9.4.8). ``tenant`` is the only
    field; the spec's JSON-RPC example omits ``params`` entirely."""

    tenant: str | None = None


# ---------------------------------------------------------------------------
# Security schemes (spec section 4.5)
# ---------------------------------------------------------------------------


class APIKeySecurityScheme(A2AModel):
    """proto ``APIKeySecurityScheme`` (section 4.5.2). v1.0 renamed 0.3's OpenAPI-style
    ``in`` field to ``location``; valid values: "query", "header", "cookie"."""

    location: str
    name: str
    description: str | None = None


class HTTPAuthSecurityScheme(A2AModel):
    """proto ``HTTPAuthSecurityScheme`` (section 4.5.3)."""

    scheme: str
    description: str | None = None
    bearer_format: str | None = None


class OpenIdConnectSecurityScheme(A2AModel):
    """proto ``OpenIdConnectSecurityScheme`` (section 4.5.5)."""

    open_id_connect_url: str
    description: str | None = None


class MutualTlsSecurityScheme(A2AModel):
    """proto ``MutualTLSSecurityScheme`` (section 4.5.6)."""

    description: str | None = None


class AuthorizationCodeOAuthFlow(A2AModel):
    """proto ``AuthorizationCodeOAuthFlow`` (section 4.5.8). ``pkceRequired`` per RFC 7636."""

    authorization_url: str
    token_url: str
    scopes: dict[str, str]
    refresh_url: str | None = None
    pkce_required: bool | None = None


class ClientCredentialsOAuthFlow(A2AModel):
    """proto ``ClientCredentialsOAuthFlow`` (section 4.5.9)."""

    token_url: str
    scopes: dict[str, str]
    refresh_url: str | None = None


class ImplicitOAuthFlow(A2AModel):
    """proto ``ImplicitOAuthFlow``, deprecated in v1.0 (use Authorization Code + PKCE).
    The proto marks no field REQUIRED."""

    authorization_url: str | None = None
    refresh_url: str | None = None
    scopes: dict[str, str] | None = None


class PasswordOAuthFlow(A2AModel):
    """proto ``PasswordOAuthFlow``, deprecated in v1.0. No field REQUIRED."""

    token_url: str | None = None
    refresh_url: str | None = None
    scopes: dict[str, str] | None = None


class DeviceCodeOAuthFlow(A2AModel):
    """proto ``DeviceCodeOAuthFlow`` (section 4.5.10, RFC 8628)."""

    device_authorization_url: str
    token_url: str
    scopes: dict[str, str]
    refresh_url: str | None = None


class OAuthFlows(A2AModel):
    """proto ``OAuthFlows`` (section 4.5.7): oneof flow, exactly one member."""

    _ONEOF: ClassVar[tuple[str, ...]] = (
        "authorization_code",
        "client_credentials",
        "implicit",
        "password",
        "device_code",
    )

    authorization_code: AuthorizationCodeOAuthFlow | None = None
    client_credentials: ClientCredentialsOAuthFlow | None = None
    implicit: ImplicitOAuthFlow | None = None  # deprecated in spec
    password: PasswordOAuthFlow | None = None  # deprecated in spec
    device_code: DeviceCodeOAuthFlow | None = None

    @model_validator(mode="after")
    def _check_oneof(self) -> Self:
        _exactly_one_of(self, self._ONEOF)
        return self


class OAuth2SecurityScheme(A2AModel):
    """proto ``OAuth2SecurityScheme`` (section 4.5.4)."""

    flows: OAuthFlows
    description: str | None = None
    oauth2_metadata_url: str | None = None


class SecurityScheme(A2AModel):
    """proto ``SecurityScheme`` (section 4.5.1): discriminated union (proto oneof) based
    on the OpenAPI 3.2 Security Scheme Object; exactly one member present."""

    _ONEOF: ClassVar[tuple[str, ...]] = (
        "api_key_security_scheme",
        "http_auth_security_scheme",
        "oauth2_security_scheme",
        "open_id_connect_security_scheme",
        "mtls_security_scheme",
    )

    api_key_security_scheme: APIKeySecurityScheme | None = None
    http_auth_security_scheme: HTTPAuthSecurityScheme | None = None
    oauth2_security_scheme: OAuth2SecurityScheme | None = None
    open_id_connect_security_scheme: OpenIdConnectSecurityScheme | None = None
    mtls_security_scheme: MutualTlsSecurityScheme | None = None

    @model_validator(mode="after")
    def _check_oneof(self) -> Self:
        _exactly_one_of(self, self._ONEOF)
        return self


class SecurityRequirement(A2AModel):
    """proto ``SecurityRequirement { map<string, StringList> schemes }``.

    The spec is internally inconsistent about the wire shape (D9): strict ProtoJSON is
    ``{"schemes": {"<name>": {"list": ["scope", ...]}}}`` while the section 8.5 sample
    card shows OpenAPI-style ``{"<name>": ["scope", ...]}``. We parse both and emit the
    proto-normative form.
    """

    schemes: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_both_shapes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        raw: dict[str, Any]
        if "schemes" in value and isinstance(value["schemes"], dict):
            raw = value["schemes"]
        else:
            raw = value
        schemes: dict[str, Any] = {}
        for name, scopes in raw.items():
            if isinstance(scopes, dict) and set(scopes) <= {"list"}:
                schemes[name] = scopes.get("list", [])
            else:
                schemes[name] = scopes
        return {"schemes": schemes}

    @field_serializer("schemes", when_used="json")
    def _emit_protojson(self, schemes: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
        return {name: {"list": scopes} for name, scopes in schemes.items()}


# ---------------------------------------------------------------------------
# Agent Card (spec sections 4.4, 8)
# ---------------------------------------------------------------------------


class AgentProvider(A2AModel):
    """proto ``AgentProvider`` (section 4.4.2). Both fields REQUIRED."""

    url: str
    organization: str


class AgentExtension(A2AModel):
    """proto ``AgentExtension`` (section 4.4.4). All fields formally optional."""

    uri: str | None = None
    description: str | None = None
    required: bool | None = None
    params: Metadata | None = None


class AgentCapabilities(A2AModel):
    """proto ``AgentCapabilities`` (section 4.4.3). All optional; absent means
    unsupported (section 3.3.4 capability validation). v0.3's
    ``stateTransitionHistory`` no longer exists."""

    streaming: bool | None = None
    push_notifications: bool | None = None
    extensions: list[AgentExtension] | None = None
    extended_agent_card: bool | None = None


class AgentSkill(A2AModel):
    """proto ``AgentSkill`` (section 4.4.5)."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(min_length=1)  # REQUIRED array (section 5.7)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None
    security_requirements: list[SecurityRequirement] | None = None


class AgentInterface(A2AModel):
    """proto ``AgentInterface`` (section 4.4.6). Officially supported
    ``protocolBinding`` values: "JSONRPC", "GRPC", "HTTP+JSON" (open string).
    When ``tenant`` is set, clients MUST echo it in every request (section 8.3.2)."""

    url: str
    protocol_binding: str
    protocol_version: str  # e.g. "1.0"
    tenant: str | None = None


class AgentCardSignature(A2AModel):
    """proto ``AgentCardSignature`` (section 4.4.7): JWS per RFC 7515 over a
    JCS-canonicalized card (section 8.4). Verification is out of v0 scope."""

    protected: str  # base64url-encoded JWS protected header
    signature: str  # base64url-encoded signature
    header: Metadata | None = None


class AgentCard(A2AModel):
    """proto ``AgentCard`` (section 4.4.1), served at ``/.well-known/agent-card.json``
    (sections 8.1-8.2, 14.3).

    ``securityRequirements`` is the proto-normative JSON name; the section 8.5 sample
    card calls it ``security``. Both are accepted on parse, ``securityRequirements``
    is emitted (D9).
    """

    name: str
    description: str
    supported_interfaces: list[AgentInterface] = Field(min_length=1)  # first entry preferred
    version: str
    capabilities: AgentCapabilities
    default_input_modes: list[str] = Field(min_length=1)  # media types
    default_output_modes: list[str] = Field(min_length=1)
    skills: list[AgentSkill] = Field(min_length=1)
    provider: AgentProvider | None = None
    documentation_url: str | None = None
    security_schemes: dict[str, SecurityScheme] | None = None
    security_requirements: list[SecurityRequirement] | None = None
    signatures: list[AgentCardSignature] | None = None
    icon_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_security_alias(cls, value: Any) -> Any:
        # Section 8.5 sample card and sections 3.1.11/13.3 prose use "security" for the
        # field the proto names security_requirements (D9 / spec-notes section 9.1).
        if (
            isinstance(value, dict)
            and "security" in value
            and "securityRequirements" not in value
            and "security_requirements" not in value
        ):
            value = dict(value)
            value["securityRequirements"] = value.pop("security")
        return value


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 envelopes (spec section 9)
# ---------------------------------------------------------------------------


class JSONRPCError(A2AModel):
    """JSON-RPC 2.0 error object (section 9.5). ``data``, when present, is an array of
    objects each carrying a ``@type`` key (ProtoJSON Any), e.g. google.rpc.ErrorInfo
    with ``reason`` and ``domain: "a2a-protocol.org"``."""

    code: int
    message: str
    data: list[dict[str, Any]] | None = None


class JSONRPCRequest(A2AModel):
    """JSON-RPC 2.0 request (section 9.3). ``method`` is one of the PascalCase
    A2AMethod strings; ``params`` is the camelCase request object."""

    method: str
    id: str | int | None = None
    params: dict[str, Any] | None = None
    jsonrpc: Literal["2.0"] = "2.0"

    @field_validator("jsonrpc", mode="before")
    @classmethod
    def _default_version(cls, value: Any) -> Any:
        return "2.0" if value is None else value

    def to_wire(self) -> dict[str, Any]:
        # Spec order: jsonrpc, id, method, params (section 9.3). id is omitted for
        # notifications; params is omitted when absent.
        dumped = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        ordered: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if "id" in dumped:
            ordered["id"] = dumped["id"]
        ordered["method"] = self.method
        if "params" in dumped:
            ordered["params"] = dumped["params"]
        return ordered


class JSONRPCSuccessResponse(A2AModel):
    """JSON-RPC 2.0 success response. ``result`` is REQUIRED by JSON-RPC even when
    null (e.g. DeleteTaskPushNotificationConfig), so ``to_wire`` always includes it."""

    result: Any = None
    id: str | int | None = None
    jsonrpc: Literal["2.0"] = "2.0"

    def to_wire(self) -> dict[str, Any]:
        # Spec examples order keys jsonrpc, id, result (section 9.4.2). result and id are
        # kept even when null (JSON-RPC requires the result member on success).
        dumped = self.model_dump(mode="json", by_alias=True)
        return {"jsonrpc": self.jsonrpc, "id": dumped.get("id"), "result": dumped.get("result")}


class JSONRPCErrorResponse(A2AModel):
    """JSON-RPC 2.0 error response. ``id`` is REQUIRED (null when undetectable)."""

    error: JSONRPCError
    id: str | int | None = None
    jsonrpc: Literal["2.0"] = "2.0"

    def to_wire(self) -> dict[str, Any]:
        return {"jsonrpc": self.jsonrpc, "id": self.id, "error": self.error.to_wire()}


__all__ = [
    "A2AModel",
    "A2ATimestamp",
    "APIKeySecurityScheme",
    "AgentCapabilities",
    "AgentCard",
    "AgentCardSignature",
    "AgentExtension",
    "AgentInterface",
    "AgentProvider",
    "AgentSkill",
    "Artifact",
    "AuthenticationInfo",
    "AuthorizationCodeOAuthFlow",
    "Base64Raw",
    "CancelTaskRequest",
    "ClientCredentialsOAuthFlow",
    "DeleteTaskPushNotificationConfigRequest",
    "DeviceCodeOAuthFlow",
    "GetExtendedAgentCardRequest",
    "GetTaskPushNotificationConfigRequest",
    "GetTaskRequest",
    "HTTPAuthSecurityScheme",
    "ImplicitOAuthFlow",
    "JSONRPCError",
    "JSONRPCErrorResponse",
    "JSONRPCRequest",
    "JSONRPCSuccessResponse",
    "ListTaskPushNotificationConfigsRequest",
    "ListTaskPushNotificationConfigsResponse",
    "ListTasksRequest",
    "ListTasksResponse",
    "Message",
    "Metadata",
    "MutualTlsSecurityScheme",
    "OAuth2SecurityScheme",
    "OAuthFlows",
    "OpenIdConnectSecurityScheme",
    "Part",
    "PasswordOAuthFlow",
    "Role",
    "SecurityRequirement",
    "SecurityScheme",
    "SendMessageConfiguration",
    "SendMessageRequest",
    "SendMessageResponse",
    "SubscribeToTaskRequest",
    "Task",
    "TaskArtifactUpdateEvent",
    "TaskPushNotificationConfig",
    "TaskState",
    "TaskStatus",
    "TaskStatusUpdateEvent",
]
