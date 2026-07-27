"""``wrap()``: expose any Python callable/agent as a spec-valid A2A dev server.

    app = wrap(my_agent, name="quoting-agent", skills=["freight-quote"])

Built on a2a-sandbox's own A2A server (no fasta2a/a2a-sdk dependency — decision D-wrap), so
it stays a thin, deterministic, test-oriented dev server rather than production plumbing.
The callable receives the inbound message text (and, if it accepts a second argument, the
structured data part) and returns the result payload; a2a-sandbox turns that into a
completed A2A task with the result as an artifact. Sync or async callables both work.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from starlette.applications import Starlette

from a2a_sandbox.adapters.a2a.mockagent import default_card
from a2a_sandbox.adapters.a2a.server import A2AServer
from a2a_sandbox.core.behaviour import Complete, Directive, Fail, SessionContext, Turn

AgentCallable = Callable[..., Any] | Callable[..., Awaitable[Any]]


class _CallableBehaviour:
    """Adapts a user callable to the behaviour protocol: call it, complete with its result."""

    def __init__(self, fn: AgentCallable) -> None:
        self._fn = fn
        # Call with (text, data) if the callable accepts two positional args, else (text,).
        try:
            self._arity = len(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            self._arity = 1

    async def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        try:
            out = self._fn(turn.text, turn.data) if self._arity >= 2 else self._fn(turn.text)
            if inspect.isawaitable(out):
                out = await out
        except Exception as exc:  # a failing agent becomes a failed task, not a server 500
            return [Fail(reason=f"{type(exc).__name__}: {exc}")]
        return [Complete(result=out)]


def wrap(
    fn: AgentCallable,
    *,
    name: str,
    skills: list[str] | None = None,
    description: str | None = None,
) -> Starlette:
    """Return an ASGI app that serves ``fn`` as an A2A agent (Agent Card auto-generated).

    Run it with any ASGI server (``uvicorn.run(app, ...)``), or point an in-process
    :class:`~a2a_sandbox.adapters.a2a.client.A2AClient` at it with ``A2AClient(app=app)``.
    """
    card = default_card(name, url="http://mock.local/", skills=skills)
    if description is not None:
        card.description = description
    server = A2AServer(lambda: _CallableBehaviour(fn), card=card)
    return server.build_app()
