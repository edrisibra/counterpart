"""The mock works as the caller too, not only as the thing being called."""

from counterpart import MockAgent, wrap
from counterpart.adapters.a2a.mockagent import serve_asgi


def quoting_agent(text: str) -> dict[str, object]:
    return {"price": 1420.0, "currency": "USD"}


def broken_agent(text: str) -> dict[str, object]:
    return {"note": "done!"}  # completes, returns no price


async def test_mock_can_call_our_agent_and_check_its_answer() -> None:
    with serve_asgi(wrap(quoting_agent, name="quoting-agent")) as url:
        peer = MockAgent("cooperative")
        task = await peer.send_task(url, "Quote 2 pallets", contract={"price": float})
    assert task.status == "completed"
    assert not task.contract_violated


async def test_our_own_agent_returning_junk_is_caught() -> None:
    """The same check applied to ourselves, which is the point of testing this direction."""
    with serve_asgi(wrap(broken_agent, name="broken-agent")) as url:
        peer = MockAgent("cooperative")
        task = await peer.send_task(url, "Quote 2 pallets", contract={"price": float})
    assert task.status == "completed"  # our agent reported success
    assert task.contract_violated  # ...with nothing usable in it
