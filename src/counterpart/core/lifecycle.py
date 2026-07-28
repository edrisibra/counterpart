"""Protocol-agnostic task lifecycle: a small state machine plus an ordered timeline.

Hard rule (see package docstring): nothing here may import or assume A2A. States are
opaque strings; a protocol adapter supplies the concrete state set via ``LifecycleSpec``.
The A2A adapter, for example, builds a spec from ``TaskState`` and marks
``TASK_STATE_COMPLETED`` etc. terminal, but this module never names them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IllegalTransition(Exception):
    """Raised when a transition is rejected by the lifecycle's transition policy.

    Adversarial personas intentionally trigger illegal transitions; they pass
    ``force=True`` to :meth:`Lifecycle.transition_to` to bypass the policy on purpose.
    """


@dataclass(frozen=True)
class LifecycleSpec:
    """Describes a protocol's task lifecycle in protocol-agnostic terms.

    ``allowed`` is the transition policy: ``allowed(from_state, to_state) -> bool``.
    When ``None`` (the default) the policy is permissive-but-safe: any transition is
    allowed except leaving a terminal state. A protocol adapter may pass a stricter
    policy; the ``spec_violator``-style personas bypass it with ``force=True``.
    """

    states: frozenset[str]
    initial: str
    terminal: frozenset[str] = field(default_factory=frozenset)
    interrupted: frozenset[str] = field(default_factory=frozenset)
    allowed: Callable[[str, str], bool] | None = None

    def __post_init__(self) -> None:
        unknown = (self.terminal | self.interrupted | {self.initial}) - self.states
        if unknown:
            raise ValueError(f"states referenced but not declared: {sorted(unknown)}")
        overlap = self.terminal & self.interrupted
        if overlap:
            raise ValueError(f"states cannot be both terminal and interrupted: {sorted(overlap)}")

    def is_terminal(self, state: str) -> bool:
        return state in self.terminal

    def is_interrupted(self, state: str) -> bool:
        return state in self.interrupted

    def can_transition(self, from_state: str, to_state: str) -> bool:
        if to_state not in self.states:
            return False
        if self.allowed is not None:
            return self.allowed(from_state, to_state)
        # Default policy: you may go anywhere except out of a terminal state.
        return not self.is_terminal(from_state)


@dataclass(frozen=True)
class TimelineEntry:
    """One recorded state entry: which state, and when it was entered."""

    state: str
    at: datetime
    forced: bool = False


class Lifecycle:
    """A live task's state, with the ordered history of states it has passed through."""

    def __init__(self, spec: LifecycleSpec, *, clock: Clock = _utcnow) -> None:
        self._spec = spec
        self._clock = clock
        self._timeline: list[TimelineEntry] = [TimelineEntry(spec.initial, clock())]

    @property
    def spec(self) -> LifecycleSpec:
        return self._spec

    @property
    def state(self) -> str:
        return self._timeline[-1].state

    @property
    def timeline(self) -> tuple[TimelineEntry, ...]:
        return tuple(self._timeline)

    @property
    def is_terminal(self) -> bool:
        return self._spec.is_terminal(self.state)

    @property
    def is_interrupted(self) -> bool:
        return self._spec.is_interrupted(self.state)

    def transition_to(self, to_state: str, *, force: bool = False) -> None:
        """Move to ``to_state``, recording it on the timeline.

        Rejects illegal transitions with :class:`IllegalTransition` unless ``force`` is
        set. ``force`` is how adversarial personas emit deliberately-illegal transitions
        (and it still records them, so the timeline shows what the counterparty did).
        """
        if to_state not in self._spec.states and not force:
            raise IllegalTransition(f"{to_state!r} is not a declared state")
        if not force and not self._spec.can_transition(self.state, to_state):
            raise IllegalTransition(f"transition {self.state!r} -> {to_state!r} is not allowed")
        self._timeline.append(TimelineEntry(to_state, self._clock(), forced=force))

    def reached_state(self, state: str) -> bool:
        """True if the task has ever been in ``state`` (per the ordered timeline)."""
        return any(entry.state == state for entry in self._timeline)

    def states_visited(self) -> tuple[str, ...]:
        return tuple(entry.state for entry in self._timeline)
