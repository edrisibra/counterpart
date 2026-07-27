"""Tests for wrap(): exposing a plain callable as an A2A dev server."""

from pydantic import BaseModel

from a2a_sandbox import A2AClient, Contract, wrap


class Quote(BaseModel):
    price: float
    currency: str


def quote_agent(text: str) -> dict[str, object]:
    # A trivial "agent": always quotes a fixed price.
    assert "quote" in text.lower()
    return {"price": 1299.0, "currency": "USD"}


async def test_wrap_sync_callable_completes_with_result() -> None:
    app = wrap(quote_agent, name="quoting-agent", skills=["freight-quote"])
    async with A2AClient(app=app) as client:
        card = await client.resolve_card()
        assert card.name == "quoting-agent"
        assert card.skills[0].id == "freight-quote"
        result = await client.send_message("Please quote 2 pallets LA->Dallas")
    assert result.completed
    assert result.result == {"price": 1299.0, "currency": "USD"}


async def test_wrap_async_callable() -> None:
    async def async_agent(text: str) -> dict[str, int]:
        return {"n": len(text)}

    app = wrap(async_agent, name="len-agent")
    async with A2AClient(app=app) as client:
        result = await client.send_message("hello")
    assert result.completed
    assert result.result == {"n": 5}


async def test_wrapped_agent_failure_becomes_failed_task_not_500() -> None:
    def broken(text: str) -> dict[str, object]:
        raise RuntimeError("kaboom")

    app = wrap(broken, name="broken-agent")
    async with A2AClient(app=app) as client:
        result = await client.send_message("go")
    assert not result.completed
    assert result.status == "failed"


async def test_wrapped_agent_verified_by_contract() -> None:
    """The whole point: point a client + contract at a wrapped real agent."""
    app = wrap(quote_agent, name="quoting-agent")
    contract = Contract("quote").returns(Quote).expect_status("completed")
    async with A2AClient(app=app) as client:
        result = await client.send_message("quote LA->Dallas", contract=contract)
    assert not result.contract_violated
