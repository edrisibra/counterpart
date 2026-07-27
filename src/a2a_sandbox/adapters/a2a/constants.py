"""A2A protocol constants, verified against spec v1.0 (tag v1.0.1).

Every value here is traceable to docs/spec-notes.md, which cites the spec section it
came from. Do not "fix" a value without re-checking the spec.
"""

from enum import IntEnum, StrEnum
from typing import Final

# --- Versioning (spec section 3.6) ---
PROTOCOL_VERSION: Final = "1.0"  # Major.Minor only; patch numbers never go on the wire
SPEC_RELEASE: Final = "v1.0.1"  # the spec release these constants were verified against

# --- Service parameters (spec sections 3.2.6, 14.2) ---
A2A_VERSION_HEADER: Final = "A2A-Version"  # empty/absent MUST be interpreted as 0.3
A2A_EXTENSIONS_HEADER: Final = "A2A-Extensions"  # comma-separated extension URIs

# --- Discovery (spec sections 8.2, 14.3) ---
WELL_KNOWN_AGENT_CARD_PATH: Final = "/.well-known/agent-card.json"

# --- Media types (spec sections 9.1, 14.1.1) ---
MEDIA_TYPE_JSON: Final = "application/json"  # JSON-RPC binding
MEDIA_TYPE_A2A_JSON: Final = "application/a2a+json"  # HTTP+JSON/REST binding
MEDIA_TYPE_SSE: Final = "text/event-stream"  # streaming responses

# --- Protocol bindings (spec section 4.4.6: AgentInterface.protocolBinding) ---
BINDING_JSONRPC: Final = "JSONRPC"
BINDING_GRPC: Final = "GRPC"
BINDING_HTTP_JSON: Final = "HTTP+JSON"

JSONRPC_VERSION: Final = "2.0"


class A2AMethod(StrEnum):
    """The complete v1.0 method set (spec section 5.3 Method Mapping Reference).

    These exact PascalCase strings are the JSON-RPC ``method`` values and the gRPC rpc
    names (spec section 9.1). The v0.3 slash-style names (``message/send``, ...) do not
    exist in v1.0.
    """

    SEND_MESSAGE = "SendMessage"
    SEND_STREAMING_MESSAGE = "SendStreamingMessage"
    GET_TASK = "GetTask"
    LIST_TASKS = "ListTasks"
    CANCEL_TASK = "CancelTask"
    SUBSCRIBE_TO_TASK = "SubscribeToTask"
    CREATE_TASK_PUSH_NOTIFICATION_CONFIG = "CreateTaskPushNotificationConfig"
    GET_TASK_PUSH_NOTIFICATION_CONFIG = "GetTaskPushNotificationConfig"
    LIST_TASK_PUSH_NOTIFICATION_CONFIGS = "ListTaskPushNotificationConfigs"
    DELETE_TASK_PUSH_NOTIFICATION_CONFIG = "DeleteTaskPushNotificationConfig"
    GET_EXTENDED_AGENT_CARD = "GetExtendedAgentCard"


class A2AErrorCode(IntEnum):
    """JSON-RPC error codes: standard (spec section 9.5) + A2A-specific (section 5.4).

    A2A-specific errors use the range -32001..-32099; only -32001..-32009 are assigned
    and -32000 is not defined in v1.0.
    """

    # Standard JSON-RPC 2.0 (spec section 9.5)
    JSON_PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # A2A-specific (spec section 5.4)
    TASK_NOT_FOUND = -32001
    TASK_NOT_CANCELABLE = -32002
    PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
    UNSUPPORTED_OPERATION = -32004
    CONTENT_TYPE_NOT_SUPPORTED = -32005
    INVALID_AGENT_RESPONSE = -32006
    EXTENDED_AGENT_CARD_NOT_CONFIGURED = -32007
    EXTENSION_SUPPORT_REQUIRED = -32008
    VERSION_NOT_SUPPORTED = -32009


# Error type names exactly as the spec tables write them (sections 5.4, 9.5).
ERROR_NAMES: Final[dict[A2AErrorCode, str]] = {
    A2AErrorCode.JSON_PARSE_ERROR: "JSONParseError",
    A2AErrorCode.INVALID_REQUEST: "InvalidRequestError",
    A2AErrorCode.METHOD_NOT_FOUND: "MethodNotFoundError",
    A2AErrorCode.INVALID_PARAMS: "InvalidParamsError",
    A2AErrorCode.INTERNAL_ERROR: "InternalError",
    A2AErrorCode.TASK_NOT_FOUND: "TaskNotFoundError",
    A2AErrorCode.TASK_NOT_CANCELABLE: "TaskNotCancelableError",
    A2AErrorCode.PUSH_NOTIFICATION_NOT_SUPPORTED: "PushNotificationNotSupportedError",
    A2AErrorCode.UNSUPPORTED_OPERATION: "UnsupportedOperationError",
    A2AErrorCode.CONTENT_TYPE_NOT_SUPPORTED: "ContentTypeNotSupportedError",
    A2AErrorCode.INVALID_AGENT_RESPONSE: "InvalidAgentResponseError",
    A2AErrorCode.EXTENDED_AGENT_CARD_NOT_CONFIGURED: "ExtendedAgentCardNotConfiguredError",
    A2AErrorCode.EXTENSION_SUPPORT_REQUIRED: "ExtensionSupportRequiredError",
    A2AErrorCode.VERSION_NOT_SUPPORTED: "VersionNotSupportedError",
}

# Normative default messages exist ONLY for the five standard JSON-RPC codes
# (spec section 9.5 table, "Standard Message" column). A2A-specific errors have no
# normative message strings: match on code, never on message text.
STANDARD_ERROR_MESSAGES: Final[dict[A2AErrorCode, str]] = {
    A2AErrorCode.JSON_PARSE_ERROR: "Invalid JSON payload",
    A2AErrorCode.INVALID_REQUEST: "Request payload validation error",
    A2AErrorCode.METHOD_NOT_FOUND: "Method not found",
    A2AErrorCode.INVALID_PARAMS: "Invalid parameters",
    A2AErrorCode.INTERNAL_ERROR: "Internal error",
}

# HTTP status the same error maps to in the HTTP+JSON binding (spec section 5.4).
# Useful to the conformance checker; the JSON-RPC binding itself replies HTTP 200.
ERROR_HTTP_STATUS: Final[dict[A2AErrorCode, int]] = {
    A2AErrorCode.JSON_PARSE_ERROR: 400,
    A2AErrorCode.INVALID_REQUEST: 400,
    A2AErrorCode.METHOD_NOT_FOUND: 404,
    A2AErrorCode.INVALID_PARAMS: 400,
    A2AErrorCode.INTERNAL_ERROR: 500,
    A2AErrorCode.TASK_NOT_FOUND: 404,
    A2AErrorCode.TASK_NOT_CANCELABLE: 400,
    A2AErrorCode.PUSH_NOTIFICATION_NOT_SUPPORTED: 400,
    A2AErrorCode.UNSUPPORTED_OPERATION: 400,
    A2AErrorCode.CONTENT_TYPE_NOT_SUPPORTED: 400,
    A2AErrorCode.INVALID_AGENT_RESPONSE: 500,
    A2AErrorCode.EXTENDED_AGENT_CARD_NOT_CONFIGURED: 400,
    A2AErrorCode.EXTENSION_SUPPORT_REQUIRED: 400,
    A2AErrorCode.VERSION_NOT_SUPPORTED: 400,
}


def error_reason(code: A2AErrorCode) -> str:
    """``google.rpc.ErrorInfo.reason`` for an A2A error (spec sections 10.6, 11.6).

    The error type name in UPPER_SNAKE_CASE without the ``Error`` suffix, e.g.
    ``TaskNotFoundError`` -> ``TASK_NOT_FOUND``. The spec states this rule for the
    A2A-specific errors; we apply the same derivation to the standard JSON-RPC names
    (e.g. ``InternalError`` -> ``INTERNAL``). ``domain`` is always ``a2a-protocol.org``.
    """
    name = ERROR_NAMES[code].removesuffix("Error")
    # CamelCase -> UPPER_SNAKE, keeping acronym runs together ("JSONParse" -> "JSON_PARSE").
    out: list[str] = []
    for i, ch in enumerate(name):
        starts_word = ch.isupper() and i > 0
        after_lower = i > 0 and not name[i - 1].isupper()
        before_lower = i + 1 < len(name) and name[i + 1].islower()
        if starts_word and (after_lower or before_lower):
            out.append("_")
        out.append(ch)
    return "".join(out).upper()


ERROR_INFO_DOMAIN: Final = "a2a-protocol.org"
ERROR_INFO_TYPE_URL: Final = "type.googleapis.com/google.rpc.ErrorInfo"
