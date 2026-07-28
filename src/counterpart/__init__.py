"""counterpart: mock the agent on the other end of an A2A call.

A2A lets one AI agent hand work to another. counterpart stands in for the agent on
the other end, including the ways it can go wrong, and checks that what comes back
is usable. A task reaching ``completed`` only means the peer stopped working, so a
contract looks at the content of the reply rather than the status of the call.
"""

__version__ = "0.1.11"

from typing import Any

from counterpart.adapters.a2a.client import A2AClient, TaskResult
from counterpart.adapters.a2a.mockagent import MockAgent, serve_asgi
from counterpart.adapters.a2a.wrap import wrap
from counterpart.core.contract import Contract, ContractReport, FailureCategory
from counterpart.personas import available as available_personas


def mock_agent(persona: str = "cooperative", **config: Any) -> MockAgent:
    """Convenience factory mirroring the pytest fixture: ``mock_agent(persona=...)``."""
    return MockAgent(persona, **config)


__all__ = [
    "A2AClient",
    "Contract",
    "ContractReport",
    "FailureCategory",
    "MockAgent",
    "TaskResult",
    "available_personas",
    "mock_agent",
    "serve_asgi",
    "wrap",
]
