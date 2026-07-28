"""Regression tests for the pre-ship bug-hunt fixes (see git history / docs).

Each test pins a specific defect the hunt found so it can't silently return.
"""

import pytest

from counterpart import A2AClient, MockAgent, wrap
from counterpart.adapters.a2a.constants import A2AErrorCode, A2AMethod
from counterpart.adapters.a2a.types import (
    JSONRPCRequest,
    Role,
    TaskState,
)


async def _post(client: A2AClient, method: str, params: dict) -> dict:
    rpc = JSONRPCRequest(method=method, id=1, params=params)
    resp = await client._http.post("/", content=rpc.to_wire_json(), headers=client._headers())
    return resp.json()


# --- server.py: JSON parse vs invalid-request error codes (spec 9.5) -------


async def test_malformed_json_is_parse_error() -> None:
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        resp = await client._http.post("/", content=b"{not json", headers=client._headers())
    assert resp.json()["error"]["code"] == int(A2AErrorCode.JSON_PARSE_ERROR)  # -32700


async def test_wellformed_but_invalid_request_is_invalid_request() -> None:
    """Valid JSON that isn't a valid Request object → -32600, not -32700; id is recovered."""
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        # No "method" — structurally invalid Request.
        resp = await client._http.post(
            "/", content=b'{"jsonrpc":"2.0","id":7,"params":{}}', headers=client._headers()
        )
    body = resp.json()
    assert body["error"]["code"] == int(A2AErrorCode.INVALID_REQUEST)  # -32600
    assert body["id"] == 7  # recovered from the parsed request


# --- server.py: GetTask/CancelTask param validation ------------------------


async def test_get_task_missing_id_is_invalid_params() -> None:
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        body = await _post(client, A2AMethod.GET_TASK.value, {})
    assert body["error"]["code"] == int(A2AErrorCode.INVALID_PARAMS)  # -32602, not -32001


async def test_cancel_task_nonstring_id_is_invalid_params() -> None:
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        rpc = JSONRPCRequest(method=A2AMethod.CANCEL_TASK.value, id=1, params={"id": 123})
        resp = await client._http.post("/", content=rpc.to_wire_json(), headers=client._headers())
    assert resp.json()["error"]["code"] == int(A2AErrorCode.INVALID_PARAMS)


# --- server.py: mismatched contextId + taskId on a follow-up (spec 3.4.3) --


async def test_mismatched_context_id_on_followup_is_rejected() -> None:
    mock = MockAgent("clarifier", question="when?")
    async with mock.client() as client:
        first = await client.send_message("quote")
        assert first.reached_state("input-required")
        # Follow-up with the right taskId but a wrong contextId must be rejected.
        body = await _post(
            client,
            A2AMethod.SEND_MESSAGE.value,
            {
                "message": {
                    "messageId": "m2",
                    "role": Role.USER.value,
                    "parts": [{"text": "Friday"}],
                    "taskId": first.task.id,
                    "contextId": "ctx-TOTALLY-WRONG",
                }
            },
        )
    assert body["error"]["code"] == int(A2AErrorCode.INVALID_PARAMS)


async def test_matching_context_id_on_followup_is_accepted() -> None:
    mock = MockAgent("clarifier", question="when?", result={"ok": True})
    async with mock.client() as client:
        first = await client.send_message("quote")
        second = await client.reply(first.task.id, "Friday", context_id=first.task.context_id)
    assert second.completed


# --- client.py: JSON-RPC error surfaced, not swallowed ---------------------


async def test_client_surfaces_peer_jsonrpc_error() -> None:
    from counterpart.adapters.a2a.client import A2AProtocolError

    mock = MockAgent("cooperative")
    async with mock.client() as client:
        # Reference an unknown task id → server returns a JSON-RPC error over HTTP 200.
        with pytest.raises(A2AProtocolError, match="JSON-RPC error"):
            await client.send_message("hi", task_id="task-does-not-exist")


# --- client.py: streamed chunked artifacts reassembled by append ----------


def test_accumulate_artifact_merges_appended_chunks() -> None:
    """append=true extends the same artifact id; a plain update replaces/adds (spec 4.2.2)."""
    from counterpart.adapters.a2a.client import _accumulate_artifact
    from counterpart.adapters.a2a.types import Artifact, Part, TaskArtifactUpdateEvent

    acc: dict[str, Artifact] = {}

    def event(text: str, *, append: bool) -> TaskArtifactUpdateEvent:
        return TaskArtifactUpdateEvent(
            task_id="t",
            context_id="c",
            artifact=Artifact(artifact_id="art-1", parts=[Part(text=text)]),
            append=append,
        )

    _accumulate_artifact(acc, event("Hello ", append=False))
    _accumulate_artifact(acc, event("world", append=True))
    assert list(acc) == ["art-1"]  # one artifact, not two
    assert [p.text for p in acc["art-1"].parts] == ["Hello ", "world"]  # chunks reassembled


async def test_streaming_does_not_crash_and_completes() -> None:
    mock = MockAgent("cooperative", result={"ok": True})
    async with mock.client() as client:
        result = await client.send_message("go", stream=True)
    assert result.completed


# --- wrap.py: arity heuristic handles varied signatures --------------------


async def test_wrap_keyword_only_agent_not_misinvoked() -> None:
    def kw_only_agent(text, *, verbose=False):  # 1 positional + a keyword-only
        return {"echo": text, "verbose": verbose}

    async with A2AClient(app=wrap(kw_only_agent, name="kw")) as client:
        result = await client.send_message("hi")
    assert result.completed
    assert result.result == {"echo": "hi", "verbose": False}


async def test_wrap_varargs_agent_receives_data() -> None:
    def varargs_agent(*args):
        return {"nargs": len(args)}

    async with A2AClient(app=wrap(varargs_agent, name="va")) as client:
        result = await client.send_message("hi", data={"x": 1})
    assert result.completed
    assert result.result == {"nargs": 2}  # text + data both passed


# --- types.py: coerce raises ValueError (not AttributeError) on bad input ---


def test_coerce_non_string_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="unknown task state"):
        TaskState.coerce(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown role"):
        Role.coerce(None)  # type: ignore[arg-type]


# --- personas: false_success default is not shared mutable state -----------


def test_false_success_default_result_is_per_instance() -> None:
    from counterpart.personas import get_persona

    a = get_persona("false_success")
    b = get_persona("false_success")
    a._result["mutated"] = True  # type: ignore[attr-defined]
    assert "mutated" not in b._result  # type: ignore[attr-defined]


# --- super-test findings (verified locally after the workflow's verifiers were cut off) ---


async def test_non_scalar_jsonrpc_id_does_not_crash_the_error_path() -> None:
    """JSON-RPC allows only string/number/null as `id`. An object used to crash the handler
    while it was building the error response — a malformed request became a 500."""
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        for bad_id in ("{}", "[]", '{"a":1}'):
            body = f'{{"jsonrpc":"2.0","id":{bad_id},"method":"GetTask","params":{{"id":"x"}}}}'
            resp = await client._http.post("/", content=body.encode(), headers=client._headers())
            assert resp.status_code == 200, f"id={bad_id} produced HTTP {resp.status_code}"
            payload = resp.json()
            assert payload["id"] is None  # cannot be echoed, so null per spec
            assert payload["error"]["code"] in {
                int(A2AErrorCode.INVALID_REQUEST),
                int(A2AErrorCode.INVALID_PARAMS),
                int(A2AErrorCode.TASK_NOT_FOUND),
            }


def test_returns_none_raises_instead_of_silently_passing_everything() -> None:
    """The worst failure class for this library: a contract that validates nothing and
    reports satisfied. `.returns(None)` used to do exactly that."""
    import pytest as _pytest

    from counterpart import Contract

    with _pytest.raises(TypeError, match="disable structural validation"):
        Contract("x").returns(None)

    # Omitting returns() entirely is the legitimate way to check only predicates.
    report = Contract("x").require("ok", lambda r: r["ok"] is True).verify(result={"ok": True})
    assert report.satisfied
