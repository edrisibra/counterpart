"""pytest fixtures and assertion helpers for a2a-sandbox.

Exposed to pytest via the ``pytest11`` entry point declared in pyproject.toml.
"""

from a2a_sandbox.pytest_plugin.plugin import MockAgentFactory, mock_agent

__all__ = ["MockAgentFactory", "mock_agent"]
