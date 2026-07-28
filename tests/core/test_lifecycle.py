"""Tests for the protocol-agnostic lifecycle state machine."""

from datetime import UTC, datetime

import pytest

from counterpart.core import IllegalTransition, Lifecycle, LifecycleSpec, TimelineEntry


def make_spec() -> LifecycleSpec:
    # A generic lifecycle shaped like A2A's, but named in the abstract on purpose.
    return LifecycleSpec(
        states=frozenset({"submitted", "working", "input-required", "completed", "failed"}),
        initial="submitted",
        terminal=frozenset({"completed", "failed"}),
        interrupted=frozenset({"input-required"}),
    )


class _FakeClock:
    """Deterministic clock so timeline timestamps are testable."""

    def __init__(self) -> None:
        self.t = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.t = self.t.replace(second=self.t.second + 1)
        return self.t


def test_spec_rejects_undeclared_states() -> None:
    with pytest.raises(ValueError, match="not declared"):
        LifecycleSpec(states=frozenset({"a"}), initial="a", terminal=frozenset({"b"}))


def test_spec_rejects_terminal_and_interrupted_overlap() -> None:
    with pytest.raises(ValueError, match="both terminal and interrupted"):
        LifecycleSpec(
            states=frozenset({"a", "b"}),
            initial="a",
            terminal=frozenset({"b"}),
            interrupted=frozenset({"b"}),
        )


def test_starts_in_initial_state() -> None:
    lc = Lifecycle(make_spec())
    assert lc.state == "submitted"
    assert lc.states_visited() == ("submitted",)
    assert not lc.is_terminal and not lc.is_interrupted


def test_happy_path_transitions_and_timeline() -> None:
    lc = Lifecycle(make_spec(), clock=_FakeClock())
    lc.transition_to("working")
    lc.transition_to("completed")
    assert lc.state == "completed"
    assert lc.is_terminal
    assert lc.states_visited() == ("submitted", "working", "completed")
    assert lc.reached_state("working")
    assert not lc.reached_state("failed")
    # timeline is ordered with monotonically increasing timestamps
    times = [e.at for e in lc.timeline]
    assert times == sorted(times)
    assert all(isinstance(e, TimelineEntry) for e in lc.timeline)


def test_interrupted_state_classification() -> None:
    lc = Lifecycle(make_spec())
    lc.transition_to("working")
    lc.transition_to("input-required")
    assert lc.is_interrupted and not lc.is_terminal


def test_cannot_leave_terminal_state_by_default() -> None:
    lc = Lifecycle(make_spec())
    lc.transition_to("completed")
    with pytest.raises(IllegalTransition):
        lc.transition_to("working")


def test_undeclared_target_rejected() -> None:
    lc = Lifecycle(make_spec())
    with pytest.raises(IllegalTransition, match="not a declared state"):
        lc.transition_to("bogus")


def test_force_bypasses_policy_and_records_forced() -> None:
    """Adversarial personas emit illegal transitions via force=True; the timeline shows it."""
    lc = Lifecycle(make_spec())
    lc.transition_to("completed")
    lc.transition_to("working", force=True)  # illegal: leaving terminal
    assert lc.state == "working"
    assert lc.timeline[-1].forced is True
    # an entirely undeclared state can also be forced (for spec-violation stimulus)
    lc.transition_to("TASK_STATE_TELEPORTING", force=True)
    assert lc.state == "TASK_STATE_TELEPORTING"


def test_custom_transition_policy() -> None:
    spec = LifecycleSpec(
        states=frozenset({"a", "b", "c"}),
        initial="a",
        allowed=lambda frm, to: (frm, to) in {("a", "b"), ("b", "c")},
    )
    lc = Lifecycle(spec)
    lc.transition_to("b")
    with pytest.raises(IllegalTransition):
        lc.transition_to("a")  # not in the allowed set
    lc.transition_to("c")
    assert lc.states_visited() == ("a", "b", "c")
