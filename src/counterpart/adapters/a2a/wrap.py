"""``wrap()``: expose any Python callable or agent as a spec-valid A2A dev server.

    app = wrap(my_agent, name="quoting-agent", skills=["freight-quote"])

Built on counterpart's own A2A server (no fasta2a or a2a-sdk dependency, see decision D-wrap), so
it stays a thin, deterministic, test-oriented dev server rather than production plumbing.
The callable receives the inbound message text (and, if it accepts a second argument, the
structured data part) and returns the result payload; counterpart turns that into a
completed A2A task with the result as an artifact. Sync or async callables both work.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from starlette.applications import Starlette

from counterpart.adapters.a2a.mockagent import default_card
from counterpart.adapters.a2a.server import A2AServer
from counterpart.core.behaviour import Complete, Directive, Fail, SessionContext, Turn

AgentCallable = Callable[..., Any] | Callable[..., Awaitable[Any]]


def _accepts_two_positional(fn: AgentCallable) -> bool:
    """True if ``fn`` can take a second positional argument (a fixed param or ``*args``)."""
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False  # builtins or C callables with no introspectable signature: pass text only
    positional = sum(
        1
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    has_varargs = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)
    return positional >= 2 or has_varargs


class _CallableBehaviour:
    """Adapts a user callable to the behaviour protocol: call it, complete with its result."""

    def __init__(self, fn: AgentCallable) -> None:
        self._fn = fn
        # Pass turn.data as a 2nd positional arg only if the callable actually accepts one
        # (counting POSITIONAL_ONLY or POSITIONAL_OR_KEYWORD, or any *args). Keyword-only and
        # variadic signatures no longer get mis-invoked.
        self._pass_data = _accepts_two_positional(fn)

    async def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        try:
            out = self._fn(turn.text, turn.data) if self._pass_data else self._fn(turn.text)
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
    :class:`~counterpart.adapters.a2a.client.A2AClient` at it with ``A2AClient(app=app)``.
    """
    card = default_card(name, url="http://mock.local/", skills=skills)
    if description is not None:
        card.description = description
    server = A2AServer(lambda: _CallableBehaviour(fn), card=card)
    return server.build_app()
