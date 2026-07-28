"""Protocol-agnostic behaviour: deterministic scripts that drive a mock counterparty.

A ``Behaviour`` reacts to inbound turns by emitting ``Directive`` effects, an abstract
vocabulary a protocol adapter renders into wire actions (A2A task-status transitions,
artifacts, SSE, dropped connections, ...). Nothing here is A2A-specific, so the same
persona runs in-process (no server) or over any adapter.

Directives are *semantic*, not protocol strings: a behaviour says ``Complete(result=...)``,
and the A2A adapter decides that means ``TASK_STATE_COMPLETED`` + an artifact. The one
escape hatch, ``EmitRawStatus(force=True)``, lets a future spec-violating persona push an
arbitrary or illegal status through the adapter on purpose.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --- inbound ---------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    """One inbound message to the mock counterparty. ``index`` 0 is the initiating turn."""

    text: str = ""
    data: Any = None
    index: int = 0
    raw: Any = None  # an adapter may stash the original protocol object here


@dataclass
class SessionContext:
    """Per-session state a behaviour may read and carry across turns."""

    turns: list[Turn] = field(default_factory=list)
    scratch: dict[str, Any] = field(default_factory=dict)


# --- outbound directives ---------------------------------------------------


@dataclass(frozen=True)
class Directive:
    """Base class for all effects a behaviour can emit."""


@dataclass(frozen=True)
class Progress(Directive):
    """An in-progress status update (adapter: ensure 'working', stream the message)."""

    message: str = ""


@dataclass(frozen=True)
class NeedInput(Directive):
    """Pause and ask the caller for more input (adapter: interrupted or input-required)."""

    question: str


@dataclass(frozen=True)
class Deliver(Directive):
    """Emit a result artifact without ending the task (e.g. a streamed chunk)."""

    result: Any
    name: str | None = None


@dataclass(frozen=True)
class Complete(Directive):
    """Terminal success, optionally carrying the final result artifact.

    ``false_success`` completes with a missing or corrupt ``result`` on purpose: the status
    says done, the artifact is garbage, and a contract catches the gap.
    """

    result: Any = None
    name: str | None = None


@dataclass(frozen=True)
class Fail(Directive):
    """Terminal failure with a reason."""

    reason: str = ""


@dataclass(frozen=True)
class Wait(Directive):
    """Delay before the next directive: stalling or slow-streaming (resource_abuse)."""

    seconds: float


@dataclass(frozen=True)
class Drop(Directive):
    """Sever the connection mid-exchange (flaky or resource_abuse)."""


@dataclass(frozen=True)
class EmitRawStatus(Directive):
    """Escape hatch: push an arbitrary status string through the adapter.

    ``force=True`` asks the adapter to bypass its transition policy. That is how a
    spec-violating persona emits an illegal status on purpose.
    """

    status: str
    message: str | None = None
    force: bool = True


# --- behaviour -------------------------------------------------------------


# respond may be sync (built-in personas) or async (e.g. wrap() around an async agent).
RespondResult = Sequence[Directive] | Awaitable[Sequence[Directive]]


@runtime_checkable
class Behaviour(Protocol):
    """A deterministic reaction to an inbound turn. Same turns in → same directives out.

    ``respond`` may return directives directly or an awaitable of them (so ``wrap()`` can
    call an async agent). Adapters await the result if needed.
    """

    def respond(self, turn: Turn, ctx: SessionContext) -> RespondResult: ...


def run_behaviour(behaviour: Behaviour, turns: Sequence[Turn]) -> list[list[Directive]]:
    """Drive a SYNCHRONOUS ``behaviour`` through ``turns`` in-process, directives per turn.

    The protocol-free execution path: no server, proves a persona's logic on its own. Raises
    if the behaviour is async (use the A2A adapter, which awaits, for those).
    """
    ctx = SessionContext()
    log: list[list[Directive]] = []
    for turn in turns:
        ctx.turns.append(turn)
        result = behaviour.respond(turn, ctx)
        if inspect.isawaitable(result):
            raise TypeError("run_behaviour is for synchronous behaviours; this one is async")
        log.append(list(result))
    return log
