"""End-to-end integration: an A2A client talks to a persona-driven MockAgent server.

These run over real A2A JSON-RPC/SSE via httpx's ASGI transport (no socket) — the library
testing itself, per the brief. The flagship test proves the lying-peer catch over the actual
protocol, not just in-process directives.
"""

import pytest
from pydantic import BaseModel

from counterpart import Contract, MockAgent
from counterpart.adapters.a2a.constants import A2AMethod
from counterpart.adapters.a2a.types import (
    GetTaskRequest,
    JSONRPCRequest,
    JSONRPCSuccessResponse,
)


class Quote(BaseModel):
    price: float
    currency: str


def quote_contract() -> Contract:
    return (
        Contract("freight quote LA->Dallas")
        .returns(Quote)
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .require("price_positive", lambda q: q.price > 0)
        .expect_status("completed")
    )


async def test_cooperative_completes_with_valid_result() -> None:
    mock = MockAgent("cooperative", result={"price": 1450.0, "currency": "USD"})
    async with mock.client() as client:
        card = await client.resolve_card()
        assert card.capabilities.streaming is True
        result = await client.send_message("Quote 2 pallets LA->Dallas")
    assert result.completed
    assert result.status == "completed"
    assert result.result == {"price": 1450.0, "currency": "USD"}
    report = quote_contract().verify(result=result.result, reported_status=result.status)
    assert report.satisfied


async def test_flagship_false_success_caught_over_a2a() -> None:
    """THE killer test, over real A2A: peer reports completed, returns garbage, contract catches."""
    mock = MockAgent("false_success")
    async with mock.client() as client:
        result = await client.send_message("Quote 2 pallets LA->Dallas", contract=quote_contract())
    # The peer really did report success at the protocol level...
    assert result.status == "completed"
    assert result.completed
    # ...but the returned work does not hold up, and we caught it in one assertion.
    assert result.contract_violated
    assert result.report is not None and result.report.reported_status == "completed"


async def test_clarifier_input_required_then_completes() -> None:
    mock = MockAgent(
        "clarifier", question="Deliver by when?", result={"price": 1.0, "currency": "USD"}
    )
    async with mock.client() as client:
        first = await client.send_message("Quote 2 pallets LA->Dallas")
        assert first.reached_state("input-required")
        assert not first.completed
        assert first.task.status.message is not None
        assert "when" in "".join(p.text or "" for p in first.task.status.message.parts).lower()
        second = await client.reply(first.task.id, "Friday", context_id=first.task.context_id)
    assert second.completed


async def test_streaming_sees_ordered_events() -> None:
    mock = MockAgent("cooperative", result={"price": 10.0, "currency": "USD"})
    async with mock.client() as client:
        result = await client.send_message("go", stream=True)
    assert result.completed
    # A task-lifecycle stream begins with the Task, then progresses to a terminal state.
    assert result.states[0] in {"submitted", "working"}
    assert result.states[-1] == "completed"
    assert result.result == {"price": 10.0, "currency": "USD"}


async def test_false_success_over_streaming_also_caught() -> None:
    mock = MockAgent("false_success")
    async with mock.client() as client:
        result = await client.send_message("go", stream=True, contract=quote_contract())
    assert result.contract_violated


async def test_resource_abuse_does_not_complete_synchronously() -> None:
    # returnImmediately-style: with a stalling peer, a blocking send must not hang forever;
    # here the persona emits progress + a (short) wait and never completes.
    mock = MockAgent("resource_abuse", forever=True, stall_seconds=0.01, chunks=1)
    async with mock.client() as client:
        result = await client.send_message("go")
    assert not result.completed
    assert result.status in {"working", "submitted"}


async def test_get_task_after_send() -> None:
    mock = MockAgent("cooperative", result={"ok": True})
    async with mock.client() as client:
        sent = await client.send_message("go")
        rpc = JSONRPCRequest(
            method=A2AMethod.GET_TASK.value, id=2, params=GetTaskRequest(id=sent.task.id).to_wire()
        )
        resp = await client._http.post("/", content=rpc.to_wire_json(), headers=client._headers())
    envelope = JSONRPCSuccessResponse.from_wire(resp.content)
    assert envelope.result["task"]["id"] == sent.task.id


async def test_unknown_task_id_is_task_not_found() -> None:
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        rpc = JSONRPCRequest(
            method=A2AMethod.GET_TASK.value, id=3, params=GetTaskRequest(id="task-nope").to_wire()
        )
        resp = await client._http.post("/", content=rpc.to_wire_json(), headers=client._headers())
    body = resp.json()
    assert body["error"]["code"] == -32001  # TaskNotFoundError


async def test_agent_card_served_at_well_known_path() -> None:
    mock = MockAgent("cooperative")
    async with mock.client() as client:
        resp = await client._http.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "mock-cooperative"


@pytest.mark.parametrize("persona", ["cooperative", "false_success", "clarifier"])
async def test_every_persona_serves_a_valid_first_response(persona: str) -> None:
    mock = MockAgent(persona)
    async with mock.client() as client:
        result = await client.send_message("hello")
    # Whatever the persona does, the first response is a well-formed task in a real A2A state.
    assert result.task.id
    assert result.task.context_id
    assert result.status in {"completed", "input-required", "working", "failed", "submitted"}
