"""pytest plugin: the ``mock_agent`` fixture, registered automatically on install.

Installing a2a-sandbox is the whole setup. No conftest.py, no ini options — write a test::

    async def test_survives_a_lying_peer(mock_agent):
        peer = mock_agent(persona="false_success")
        async with peer.client() as client:
            task = await client.send_message("Quote 2 pallets LA->Dallas", contract=my_contract)
        assert task.contract_violated

The fixture is a *factory*, not a single agent, so one test can stand up several
counterparties.

Async tests work out of the box because this plugin enables pytest-asyncio's ``auto`` mode
when the user has not chosen a mode themselves — see :func:`pytest_configure`. Every A2A
interaction is async (it is a network protocol), so requiring each user to discover
``asyncio_mode`` before their first test is friction this library should absorb, not pass on.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import pytest

from a2a_sandbox.adapters.a2a.mockagent import MockAgent


def pytest_configure(config: pytest.Config) -> None:
    """Enable pytest-asyncio's ``auto`` mode unless the user has set a mode explicitly.

    Without this, a bare ``async def test_...`` is not collected as a coroutine test and
    pytest reports "async def functions are not natively supported" — the first thing a new
    user would hit, in the quickstart, before seeing anything work.

    We only fill in the default. An explicit ``asyncio_mode`` in pytest.ini / pyproject.toml /
    setup.cfg, or ``-o asyncio_mode=strict`` on the command line, always wins.
    """
    try:
        explicit = config.getini("asyncio_mode")
    except ValueError:
        return  # pytest-asyncio not installed; nothing to configure
    # `getini` returns the ini default ("strict") when the user set nothing, so distinguish a
    # real user choice by looking at the parsed ini/CLI sources directly.
    user_set = "asyncio_mode" in config.inicfg or any(
        opt.startswith("asyncio_mode") for opt in config.getoption("override_ini", default=[]) or []
    )
    if not user_set and explicit != "auto":
        config.inicfg["asyncio_mode"] = "auto"
        # pytest-asyncio reads the option through the ini plugin, so update the parsed value it
        # will actually consult.
        config.option.asyncio_mode = "auto"


class MockAgentFactory(Protocol):
    def __call__(self, persona: str = ..., **config: Any) -> MockAgent: ...


@pytest.fixture
def mock_agent() -> Iterator[MockAgentFactory]:
    """Factory fixture: ``mock_agent(persona="false_success", **config)`` -> MockAgent.

    Agents are created lazily. ``.client()`` agents are in-process (ASGI, no socket) and need
    no teardown; ``.serve()`` is a context manager that cleans up its own port.
    """
    created: list[MockAgent] = []

    def factory(persona: str = "cooperative", **config: Any) -> MockAgent:
        agent = MockAgent(persona, **config)
        created.append(agent)
        return agent

    yield factory
    created.clear()
