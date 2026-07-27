"""The mock_agent fixture works, and the brief's quickstart runs verbatim through it."""

from pydantic import BaseModel

from a2a_sandbox import Contract
from a2a_sandbox.pytest_plugin import MockAgentFactory


class Quote(BaseModel):
    price: float
    currency: str


def test_fixture_is_a_factory(mock_agent: MockAgentFactory) -> None:
    a = mock_agent(persona="cooperative")
    b = mock_agent(persona="false_success")
    assert a is not b
    assert a.persona_name == "cooperative"
    assert b.persona_name == "false_success"


async def test_quickstart_lying_peer(mock_agent: MockAgentFactory) -> None:
    """The scary flagship, written the way a user would, via the fixture."""
    contract = (
        Contract("freight quote")
        .returns(Quote)
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .expect_status("completed")
    )
    peer = mock_agent(persona="false_success")
    async with peer.client() as client:
        task = await client.send_message("Quote 2 pallets LA->Dallas", contract=contract)
    assert task.contract_violated  # my agent should NOT accept this
    assert task.status == "completed"  # ...even though the peer claimed success


async def test_clarifier_reached_state(mock_agent: MockAgentFactory) -> None:
    peer = mock_agent(persona="clarifier", question="Deliver by when?")
    async with peer.client() as client:
        task = await client.send_message("Quote 2 pallets LA->Dallas")
    assert task.reached_state("input-required")
    assert not task.completed
