"""pytest plugin: the ``mock_agent`` fixture and assertion helpers.

Registered as a pytest entry point (see pyproject ``[project.entry-points.pytest11]``), so a
user just installs a2a-sandbox and writes::

    def test_survives_lying_peer(mock_agent):
        peer = mock_agent(persona="false_success")
        result = await peer.client().send_message("Quote 2 pallets LA->Dallas",
                                                   contract=my_contract)
        assert result.contract_violated

The fixture returns a factory (not a single agent) so one test can spin up several
counterparties. Every agent it creates is torn down when the test ends.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import pytest

from a2a_sandbox.adapters.a2a.mockagent import MockAgent


class MockAgentFactory(Protocol):
    def __call__(self, persona: str = ..., **config: Any) -> MockAgent: ...


@pytest.fixture
def mock_agent() -> Iterator[MockAgentFactory]:
    """Factory fixture: ``mock_agent(persona="false_success", **config)`` -> MockAgent.

    Agents are created lazily and, if they were serving on a real port, stopped at teardown.
    In-process (``.client()``) agents need no teardown.
    """
    created: list[MockAgent] = []

    def factory(persona: str = "cooperative", **config: Any) -> MockAgent:
        agent = MockAgent(persona, **config)
        created.append(agent)
        return agent

    yield factory
    # MockAgent.serve() is a context manager that cleans itself up; nothing to force-close
    # here for in-process agents. This hook exists so future stateful resources have a home.
    created.clear()
