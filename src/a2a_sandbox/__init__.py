"""a2a_sandbox: test your A2A agent against simulated counterparty agents.

Test your A2A agent against simulated counterparties — cooperative, broken, or
hostile — before you connect it to a real one. The centerpiece is catching a peer
that reports success while returning incomplete or corrupt work (silent partial
completion), via declarative contract assertions and an adversarial persona suite.
"""

__version__ = "0.0.1"

from typing import Any

from a2a_sandbox.adapters.a2a.client import A2AClient, TaskResult
from a2a_sandbox.adapters.a2a.mockagent import MockAgent
from a2a_sandbox.adapters.a2a.wrap import wrap
from a2a_sandbox.core.contract import Contract, ContractReport, FailureCategory
from a2a_sandbox.personas import available as available_personas


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
    "wrap",
]
