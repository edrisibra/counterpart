"""counterpart: test your A2A agent against simulated counterparty agents.

Test your A2A agent against simulated counterparties — cooperative, broken, or
hostile — before you connect it to a real one. The centerpiece is catching a peer
that reports success while returning incomplete or corrupt work (silent partial
completion), via declarative contract assertions and an adversarial persona suite.
"""

__version__ = "0.1.3"

from typing import Any

from counterpart.adapters.a2a.client import A2AClient, TaskResult
from counterpart.adapters.a2a.mockagent import MockAgent
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
    "wrap",
]
