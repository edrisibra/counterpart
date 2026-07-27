"""Constants verified against the vendored normative proto and spec tables."""

import re

from a2a_sandbox.adapters.a2a import (
    ERROR_HTTP_STATUS,
    ERROR_NAMES,
    STANDARD_ERROR_MESSAGES,
    WELL_KNOWN_AGENT_CARD_PATH,
    A2AErrorCode,
    A2AMethod,
    error_reason,
)


def test_method_set_matches_proto_rpcs(vendored_proto: str) -> None:
    """Spec section 9.1: JSON-RPC method names are PascalCase matching gRPC rpc names."""
    proto_rpcs = set(re.findall(r"^\s*rpc\s+(\w+)\(", vendored_proto, re.MULTILINE))
    assert proto_rpcs == {m.value for m in A2AMethod}
    assert len(A2AMethod) == 11


def test_legacy_slash_methods_do_not_exist() -> None:
    """The v0.3 slash-style names were removed in v1.0."""
    values = {m.value for m in A2AMethod}
    for legacy in ("message/send", "message/stream", "tasks/get", "tasks/resubscribe"):
        assert legacy not in values


def test_error_codes_exact() -> None:
    """Spec sections 5.4 and 9.5: the complete assigned code set. -32000 is not defined."""
    expected = {
        "JSONParseError": -32700,
        "InvalidRequestError": -32600,
        "MethodNotFoundError": -32601,
        "InvalidParamsError": -32602,
        "InternalError": -32603,
        "TaskNotFoundError": -32001,
        "TaskNotCancelableError": -32002,
        "PushNotificationNotSupportedError": -32003,
        "UnsupportedOperationError": -32004,
        "ContentTypeNotSupportedError": -32005,
        "InvalidAgentResponseError": -32006,
        "ExtendedAgentCardNotConfiguredError": -32007,
        "ExtensionSupportRequiredError": -32008,
        "VersionNotSupportedError": -32009,
    }
    assert {ERROR_NAMES[code]: int(code) for code in A2AErrorCode} == expected
    assert -32000 not in {int(code) for code in A2AErrorCode}


def test_standard_messages_only_for_jsonrpc_codes() -> None:
    """Only the five standard JSON-RPC codes have normative message strings (section 9.5)."""
    assert set(STANDARD_ERROR_MESSAGES) == {
        A2AErrorCode.JSON_PARSE_ERROR,
        A2AErrorCode.INVALID_REQUEST,
        A2AErrorCode.METHOD_NOT_FOUND,
        A2AErrorCode.INVALID_PARAMS,
        A2AErrorCode.INTERNAL_ERROR,
    }
    assert STANDARD_ERROR_MESSAGES[A2AErrorCode.JSON_PARSE_ERROR] == "Invalid JSON payload"
    assert (
        STANDARD_ERROR_MESSAGES[A2AErrorCode.INVALID_REQUEST] == "Request payload validation error"
    )


def test_http_status_mapping() -> None:
    """Spec section 5.4 error-code mapping table (HTTP column)."""
    assert ERROR_HTTP_STATUS[A2AErrorCode.TASK_NOT_FOUND] == 404
    assert ERROR_HTTP_STATUS[A2AErrorCode.TASK_NOT_CANCELABLE] == 400
    assert ERROR_HTTP_STATUS[A2AErrorCode.INVALID_AGENT_RESPONSE] == 500
    assert ERROR_HTTP_STATUS[A2AErrorCode.VERSION_NOT_SUPPORTED] == 400
    assert set(ERROR_HTTP_STATUS) == set(A2AErrorCode)


def test_error_reason_strings() -> None:
    """Sections 10.6/11.6: UPPER_SNAKE_CASE without the 'Error' suffix."""
    assert error_reason(A2AErrorCode.TASK_NOT_FOUND) == "TASK_NOT_FOUND"
    assert error_reason(A2AErrorCode.TASK_NOT_CANCELABLE) == "TASK_NOT_CANCELABLE"
    assert error_reason(A2AErrorCode.PUSH_NOTIFICATION_NOT_SUPPORTED) == (
        "PUSH_NOTIFICATION_NOT_SUPPORTED"
    )
    assert error_reason(A2AErrorCode.VERSION_NOT_SUPPORTED) == "VERSION_NOT_SUPPORTED"
    assert error_reason(A2AErrorCode.INTERNAL_ERROR) == "INTERNAL"
    assert error_reason(A2AErrorCode.JSON_PARSE_ERROR) == "JSON_PARSE"


def test_well_known_path() -> None:
    """Spec sections 8.2 / 14.3."""
    assert WELL_KNOWN_AGENT_CARD_PATH == "/.well-known/agent-card.json"
