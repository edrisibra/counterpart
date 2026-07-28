"""Protocol model tests.

Fixtures marked "spec section 6.x" are taken from the spec's worked examples. Those
examples are non-normative and sometimes omit proto-REQUIRED fields (e.g. messageId,
artifactId); where we had to add one to satisfy the normative proto, the addition is
called out with a comment.
"""

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from counterpart.adapters.a2a import (
    AgentCard,
    Artifact,
    JSONRPCError,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCSuccessResponse,
    Message,
    Part,
    Role,
    SecurityRequirement,
    SecurityScheme,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def _proto_enum_values(proto: str, enum_name: str) -> set[str]:
    block = re.search(rf"enum {enum_name} \{{(.*?)\}}", proto, re.DOTALL)
    assert block is not None, f"enum {enum_name} not found in vendored proto"
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*\d+", block.group(1), re.MULTILINE))


def test_task_state_values_match_proto(vendored_proto: str) -> None:
    assert _proto_enum_values(vendored_proto, "TaskState") == {s.value for s in TaskState}
    assert len(TaskState) == 9


def test_role_values_match_proto(vendored_proto: str) -> None:
    assert _proto_enum_values(vendored_proto, "Role") == {r.value for r in Role}


def test_task_state_classification() -> None:
    """Terminal/interrupted classification per spec sections 3.1.x / 3.2.2."""
    terminal = {s for s in TaskState if s.is_terminal}
    interrupted = {s for s in TaskState if s.is_interrupted}
    assert terminal == {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELED,
        TaskState.REJECTED,
    }
    assert interrupted == {TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED}
    assert not terminal & interrupted


def test_task_state_aliases_and_coercion() -> None:
    assert TaskState.INPUT_REQUIRED.alias == "input-required"
    assert TaskState.coerce("input-required") is TaskState.INPUT_REQUIRED
    assert TaskState.coerce("input_required") is TaskState.INPUT_REQUIRED
    assert TaskState.coerce("TASK_STATE_WORKING") is TaskState.WORKING
    assert TaskState.coerce(TaskState.WORKING) is TaskState.WORKING
    with pytest.raises(ValueError, match="unknown task state"):
        TaskState.coerce("running")  # section 6.5's example of an invalid value


def test_role_coercion() -> None:
    assert Role.coerce("user") is Role.USER
    assert Role.coerce("ROLE_AGENT") is Role.AGENT
    with pytest.raises(ValueError, match="unknown role"):
        Role.coerce("assistant")


def test_integer_enum_values_rejected() -> None:
    """Design decision D5: ProtoJSON integer enums are not accepted in v0."""
    with pytest.raises(ValidationError):
        TaskStatus.from_wire({"state": 2})


# ---------------------------------------------------------------------------
# Spec worked examples (section 6)
# ---------------------------------------------------------------------------

# Spec section 6.1 request body, verbatim.
BASIC_SEND = {
    "message": {
        "role": "ROLE_USER",
        "parts": [{"text": "What is the weather today?"}],
        "messageId": "msg-uuid",
    }
}


def test_basic_send_roundtrip() -> None:
    request = SendMessageRequest.from_wire(BASIC_SEND)
    assert request.message.role is Role.USER
    assert request.message.parts[0].text == "What is the weather today?"
    assert request.to_wire() == BASIC_SEND


# Spec section 6.3 input-required response; "messageId" added (REQUIRED per proto,
# omitted by the non-normative example).
INPUT_REQUIRED_RESPONSE = {
    "task": {
        "id": "task-uuid",
        "status": {
            "state": "TASK_STATE_INPUT_REQUIRED",
            "message": {
                "role": "ROLE_AGENT",
                "parts": [{"text": "I need more details. Where are you flying from?"}],
                "messageId": "msg-agent-1",  # added: REQUIRED in proto
            },
        },
    }
}


def test_input_required_flow() -> None:
    response = SendMessageResponse.from_wire(INPUT_REQUIRED_RESPONSE)
    task = response.payload
    assert isinstance(task, Task)
    assert task.status.state is TaskState.INPUT_REQUIRED
    assert task.status.state.is_interrupted and not task.status.state.is_terminal
    assert task.status.message is not None
    assert task.status.message.role is Role.AGENT


# Spec section 6.3 follow-up request, verbatim: the client continues the task by
# sending a new Message carrying the existing task id in "taskId" (section 3.4.3).
FOLLOW_UP = {
    "message": {
        "taskId": "task-uuid",
        "role": "ROLE_USER",
        "parts": [{"text": "From San Francisco to New York"}],
        "messageId": "msg-2",
    }
}


def test_follow_up_message_carries_task_id() -> None:
    request = SendMessageRequest.from_wire(FOLLOW_UP)
    assert request.message.task_id == "task-uuid"
    assert request.to_wire() == FOLLOW_UP


# Spec section 6.2 SSE data payloads. The artifactUpdate example omits the REQUIRED
# artifactId and contextId fields; added here.
SSE_EVENTS = [
    {"task": {"id": "task-uuid", "status": {"state": "TASK_STATE_WORKING"}}},
    {
        "artifactUpdate": {
            "taskId": "task-uuid",
            "contextId": "ctx-uuid",  # added: REQUIRED in proto
            "artifact": {
                "artifactId": "artifact-1",  # added: REQUIRED in proto
                "parts": [{"text": "# Climate Change Report\n\n"}],
            },
        }
    },
    {
        "statusUpdate": {
            "taskId": "task-uuid",
            "contextId": "ctx-uuid",  # added: REQUIRED in proto
            "status": {"state": "TASK_STATE_COMPLETED"},
        }
    },
]


def test_stream_response_events() -> None:
    kinds = []
    for event in SSE_EVENTS:
        response = StreamResponse.from_wire(event)
        kinds.append(type(response.payload).__name__)
        assert response.to_wire() == event
    assert kinds == ["Task", "TaskArtifactUpdateEvent", "TaskStatusUpdateEvent"]


def test_stream_response_has_no_kind_or_final_fields() -> None:
    """v1.0 removed the kind discriminator and the final flag (Appendix A.2.1)."""
    event = StreamResponse.from_wire(SSE_EVENTS[2])
    wire = event.to_wire()
    assert "kind" not in wire and "kind" not in wire["statusUpdate"]
    assert "final" not in wire["statusUpdate"]


# ---------------------------------------------------------------------------
# oneof enforcement
# ---------------------------------------------------------------------------


def test_stream_response_oneof_enforced() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        StreamResponse.from_wire({})
    with pytest.raises(ValidationError, match="exactly one"):
        StreamResponse.from_wire(
            {
                "task": SSE_EVENTS[0]["task"],
                "statusUpdate": SSE_EVENTS[2]["statusUpdate"],
            }
        )


def test_send_message_response_oneof_enforced() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        SendMessageResponse.from_wire({})


def test_part_oneof_enforced() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Part.from_wire({"filename": "no-content.bin"})
    with pytest.raises(ValidationError, match="exactly one"):
        Part.from_wire({"text": "hi", "url": "https://example.com/f"})


# ---------------------------------------------------------------------------
# Part variants (spec section 4.1.6, Appendix A.2.1 examples)
# ---------------------------------------------------------------------------


def test_text_part() -> None:
    part = Part.from_wire({"text": "Hello, world!"})
    assert part.text == "Hello, world!"
    assert part.to_wire() == {"text": "Hello, world!"}


def test_file_part_with_raw_bytes() -> None:
    # Appendix A.2.1 "Current Pattern" example shape.
    part = Part.from_wire(
        {"raw": "iVBORw0KGgo=", "filename": "diagram.png", "mediaType": "image/png"}
    )
    assert part.raw == b"\x89PNG\r\n\x1a\n"
    assert part.media_type == "image/png"
    assert part.to_wire() == {
        "raw": "iVBORw0KGgo=",
        "filename": "diagram.png",
        "mediaType": "image/png",
    }


def test_raw_accepts_unpadded_and_urlsafe_base64() -> None:
    """D10: emit standard padded base64; accept unpadded and URL-safe input."""
    padded = Part.from_wire({"raw": "+/x6qg=="})
    unpadded = Part.from_wire({"raw": "+/x6qg"})
    urlsafe = Part.from_wire({"raw": "-_x6qg"})
    assert padded.raw == unpadded.raw == urlsafe.raw
    for part in (padded, unpadded, urlsafe):
        assert part.to_wire() == {"raw": "+/x6qg=="}


def test_raw_rejects_garbage() -> None:
    with pytest.raises(ValidationError, match="base64"):
        Part.from_wire({"raw": "not base64!!!"})


def test_file_part_with_url() -> None:
    part = Part.from_wire({"url": "https://example.com/report.pdf", "mediaType": "application/pdf"})
    assert part.url == "https://example.com/report.pdf"


def test_data_part_accepts_any_json_value() -> None:
    """section 4.1.6: data is 'object, array, string, number, boolean, or null'
    (null is the documented D12 limitation and treated as absent)."""
    for value in ({"a": 1}, [1, 2], "s", 3.5, True):
        part = Part.from_wire({"data": value, "mediaType": "application/json"})
        assert part.data == value


# ---------------------------------------------------------------------------
# Required fields / arrays (spec section 5.7)
# ---------------------------------------------------------------------------


def test_message_requires_at_least_one_part() -> None:
    with pytest.raises(ValidationError):
        Message.from_wire({"messageId": "m1", "role": "ROLE_USER", "parts": []})


def test_message_requires_message_id() -> None:
    with pytest.raises(ValidationError):
        Message.from_wire({"role": "ROLE_USER", "parts": [{"text": "hi"}]})


def test_artifact_requires_id_and_parts() -> None:
    with pytest.raises(ValidationError):
        Artifact.from_wire({"parts": [{"text": "x"}]})
    with pytest.raises(ValidationError):
        Artifact.from_wire({"artifactId": "a1", "parts": []})


def test_unrecognized_fields_ignored() -> None:
    """section 5.7: receivers SHOULD ignore unrecognized fields (D4)."""
    task = Task.from_wire(
        {
            "id": "t1",
            "status": {"state": "TASK_STATE_WORKING"},
            "kind": "task",  # v0.3 leftover a peer might still send
            "somethingNew": {"x": 1},
        }
    )
    assert "kind" not in task.to_wire()


# ---------------------------------------------------------------------------
# Timestamps (spec section 5.6.1)
# ---------------------------------------------------------------------------


def test_timestamp_emits_utc_millisecond_z_format() -> None:
    status = TaskStatus(
        state=TaskState.WORKING,
        timestamp=datetime(2023, 10, 27, 10, 0, 0, 123456, tzinfo=UTC),
    )
    wire = status.to_wire()
    assert wire["timestamp"] == "2023-10-27T10:00:00.123Z"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", wire["timestamp"])


def test_timestamp_parses_spec_example() -> None:
    """The spec's own example uses second precision: "2023-10-27T10:00:00Z"."""
    status = TaskStatus.from_wire(
        {"state": "TASK_STATE_WORKING", "timestamp": "2023-10-27T10:00:00Z"}
    )
    assert status.timestamp == datetime(2023, 10, 27, 10, 0, tzinfo=UTC)
    assert status.to_wire()["timestamp"] == "2023-10-27T10:00:00.000Z"


def test_naive_timestamp_treated_as_utc() -> None:
    status = TaskStatus(state=TaskState.WORKING, timestamp=datetime(2024, 1, 2, 3, 4, 5))
    assert status.to_wire()["timestamp"] == "2024-01-02T03:04:05.000Z"


# ---------------------------------------------------------------------------
# Agent Card (spec sections 4.4, 8.5)
# ---------------------------------------------------------------------------

# Structured after the section 8.5 sample card (not verbatim — the sample uses the
# "security" key and OpenAPI-style requirement shape; both are exercised here).
SAMPLE_CARD = {
    "name": "GeoSpatial Route Planner Agent",
    "description": "Provides advanced route planning and mapping services.",
    "supportedInterfaces": [
        {
            "url": "https://georoute-agent.example.com/a2a/v1",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        },
        {
            "url": "https://georoute-agent.example.com/a2a/grpc",
            "protocolBinding": "GRPC",
            "protocolVersion": "1.0",
        },
    ],
    "provider": {
        "organization": "Example Geo Services Inc.",
        "url": "https://www.examplegeoservices.com",
    },
    "iconUrl": "https://georoute-agent.example.com/icon.png",
    "version": "1.2.0",
    "documentationUrl": "https://docs.examplegeoservices.com/georoute-agent/api",
    "capabilities": {"streaming": True, "pushNotifications": True, "extendedAgentCard": False},
    "securitySchemes": {
        "google": {
            "openIdConnectSecurityScheme": {
                "openIdConnectUrl": "https://accounts.google.com/.well-known/openid-configuration"
            }
        }
    },
    "security": [{"google": ["openid", "profile", "email"]}],
    "defaultInputModes": ["application/json", "text/plain"],
    "defaultOutputModes": ["application/json", "image/png"],
    "skills": [
        {
            "id": "route-optimizer-traffic",
            "name": "Traffic-Aware Route Optimizer",
            "description": "Calculates the optimal driving route.",
            "tags": ["maps", "routing"],
            "examples": ["Plan a route from SF to LA"],
        }
    ],
}


def test_agent_card_parses_sample_shape() -> None:
    card = AgentCard.from_wire(SAMPLE_CARD)
    assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.capabilities.streaming is True
    scheme = card.security_schemes["google"] if card.security_schemes else None
    assert isinstance(scheme, SecurityScheme)
    assert scheme.open_id_connect_security_scheme is not None
    # The sample's "security" key + OpenAPI-style shape landed in security_requirements.
    assert card.security_requirements is not None
    assert card.security_requirements[0].schemes == {"google": ["openid", "profile", "email"]}


def test_agent_card_emits_proto_normative_names() -> None:
    """D9: emit "securityRequirements" with the strict-ProtoJSON StringList shape."""
    card = AgentCard.from_wire(SAMPLE_CARD)
    wire = card.to_wire()
    assert "security" not in wire
    assert wire["securityRequirements"] == [
        {"schemes": {"google": {"list": ["openid", "profile", "email"]}}}
    ]


def test_agent_card_accepts_proto_form_security_requirements() -> None:
    card_data = dict(SAMPLE_CARD)
    del card_data["security"]
    card_data["securityRequirements"] = [{"schemes": {"google": {"list": ["openid"]}}}]
    card = AgentCard.from_wire(card_data)
    assert card.security_requirements is not None
    assert card.security_requirements[0].schemes == {"google": ["openid"]}


def test_agent_card_required_arrays_enforced() -> None:
    incomplete = dict(SAMPLE_CARD)
    incomplete["skills"] = []
    with pytest.raises(ValidationError):
        AgentCard.from_wire(incomplete)
    missing = {k: v for k, v in SAMPLE_CARD.items() if k != "capabilities"}
    with pytest.raises(ValidationError):
        AgentCard.from_wire(missing)


def test_security_requirement_standalone_shapes() -> None:
    flat = SecurityRequirement.from_wire({"google": ["openid"]})
    proto = SecurityRequirement.from_wire({"schemes": {"google": {"list": ["openid"]}}})
    plain_lists = SecurityRequirement.from_wire({"schemes": {"google": ["openid"]}})
    assert flat.schemes == proto.schemes == plain_lists.schemes == {"google": ["openid"]}
    assert flat.to_wire() == {"schemes": {"google": {"list": ["openid"]}}}


def test_security_scheme_oneof_enforced() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        SecurityScheme.from_wire({})
    with pytest.raises(ValidationError, match="exactly one"):
        SecurityScheme.from_wire(
            {
                "mtlsSecurityScheme": {},
                "httpAuthSecurityScheme": {"scheme": "Bearer"},
            }
        )


# ---------------------------------------------------------------------------
# JSON-RPC envelopes (spec section 9)
# ---------------------------------------------------------------------------


def test_jsonrpc_request_wire_shape() -> None:
    request = JSONRPCRequest(method="SendMessage", id=1, params=BASIC_SEND)
    assert request.to_wire() == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": BASIC_SEND,
    }


def test_jsonrpc_success_response_keeps_null_result() -> None:
    """JSON-RPC requires the result member even when null (e.g. delete-config)."""
    response = JSONRPCSuccessResponse(id=7, result=None)
    assert response.to_wire() == {"jsonrpc": "2.0", "id": 7, "result": None}


def test_jsonrpc_error_response() -> None:
    """Error shape per section 9.5: data is an array of objects with @type keys."""
    response = JSONRPCErrorResponse(
        id=None,
        error=JSONRPCError(
            code=-32001,
            message="Task not found",
            data=[
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "TASK_NOT_FOUND",
                    "domain": "a2a-protocol.org",
                }
            ],
        ),
    )
    wire = response.to_wire()
    assert wire["id"] is None
    assert wire["error"]["code"] == -32001
    assert wire["error"]["data"][0]["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"


def test_jsonrpc_sse_envelope_roundtrip() -> None:
    """Section 9.4.2: each SSE data field is a complete JSON-RPC response whose
    result is a StreamResponse."""
    envelope = JSONRPCSuccessResponse(id=1, result=SSE_EVENTS[0])
    line = f"data: {envelope.to_wire_json()}"
    assert line.startswith('data: {"jsonrpc": "2.0"')
    parsed = JSONRPCSuccessResponse.from_wire(line.removeprefix("data: "))
    stream_response = StreamResponse.from_wire(parsed.result)
    assert isinstance(stream_response.payload, Task)
