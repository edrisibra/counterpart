"""MockAgent: the developer-facing facade for the A2A adapter, both roles.

- **Server role** — ``MockAgent(persona="false_success")`` presents a spec-valid A2A agent
  driven by that persona. ``.serve()`` runs it on a real ephemeral port (point your agent at
  the URL); ``.client()`` talks to it in-process over ASGI (fast tests, no socket).
- **Client role** — ``.send_task(target_url, text, contract=...)`` sends a task TO your
  agent and returns a verifiable :class:`TaskResult`.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from typing import Any

import uvicorn
from starlette.applications import Starlette

from a2a_sandbox.adapters.a2a.client import A2AClient, TaskResult
from a2a_sandbox.adapters.a2a.constants import BINDING_JSONRPC, PROTOCOL_VERSION
from a2a_sandbox.adapters.a2a.server import A2AServer, BehaviourFactory
from a2a_sandbox.adapters.a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a_sandbox.core.contract import Contract
from a2a_sandbox.personas import get_persona


@contextlib.contextmanager
def serve_asgi(app: Starlette, host: str = "127.0.0.1", port: int = 0) -> Iterator[str]:
    """Run any ASGI app on a real ephemeral port in a background thread; yield its base URL.

    Reused by :meth:`MockAgent.serve` and handy for serving a :func:`wrap`-ed agent in a
    test or demo (``with serve_asgi(wrap(fn, name=...)) as url: ...``).
    """
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("ASGI server failed to start within 10s")
        bound_port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://{host}:{bound_port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)


def default_card(name: str, *, url: str, skills: list[str] | None = None) -> AgentCard:
    """A minimal, spec-valid Agent Card for a mock counterparty."""
    skill_ids = skills or ["echo"]
    return AgentCard(
        name=name,
        description=f"a2a-sandbox mock counterparty ({name}).",
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding=BINDING_JSONRPC,
                protocol_version=PROTOCOL_VERSION,
            )
        ],
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(id=s, name=s.replace("_", " ").title(), description=f"{s} skill", tags=[s])
            for s in skill_ids
        ],
    )


class MockAgent:
    """A simulated A2A counterparty. See module docstring for the two roles."""

    def __init__(
        self,
        persona: str = "cooperative",
        *,
        name: str | None = None,
        skills: list[str] | None = None,
        **persona_config: Any,
    ) -> None:
        self.persona_name = persona
        self._persona_config = persona_config
        self._name = name or f"mock-{persona}"
        self._skills = skills
        # A fresh behaviour per task keeps concurrent sessions independent.
        self._factory: BehaviourFactory = lambda: get_persona(persona, **persona_config)
        self.card = default_card(self._name, url="http://mock.local/", skills=skills)
        self._server = A2AServer(self._factory, card=self.card)
        self._app: Starlette = self._server.build_app()

    @property
    def app(self) -> Starlette:
        """The ASGI app (for mounting or in-process transport)."""
        return self._app

    @property
    def received_requests(self) -> list[Any]:
        """Every JSON-RPC request this mock received — for assertions on what your agent sent."""
        return self._server.received_requests

    def client(self, **kw: Any) -> A2AClient:
        """An in-process client wired to this mock over ASGI (no socket)."""
        return A2AClient(app=self._app, **kw)

    @contextlib.contextmanager
    def serve(self, host: str = "127.0.0.1", port: int = 0) -> Iterator[str]:
        """Run this mock on a real ephemeral port; yields the base URL. Point an agent at it."""
        with serve_asgi(self._app, host=host, port=port) as base:
            # Reflect the real bound URL in the served card's interface.
            self.card.supported_interfaces[0].url = f"{base}/"
            yield base

    # -- client role -------------------------------------------------------

    async def send_task(
        self,
        target_url: str,
        text: str,
        *,
        contract: Contract[Any] | None = None,
        stream: bool = False,
        **kw: Any,
    ) -> TaskResult:
        """Send a task to the agent at ``target_url`` and return a verifiable result."""
        async with A2AClient(target_url) as client:
            return await client.send_message(text, contract=contract, stream=stream, **kw)
