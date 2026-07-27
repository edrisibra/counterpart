"""Tests for the protocol-agnostic behaviour layer."""

from collections.abc import Sequence

from a2a_sandbox.core import Complete, Directive, Progress, SessionContext, Turn, run_behaviour


class _Echo:
    """A trivial deterministic behaviour used to exercise the runner."""

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        return [Progress(f"turn {turn.index}"), Complete(result=turn.text)]


def test_run_behaviour_records_directives_per_turn() -> None:
    log = run_behaviour(_Echo(), [Turn(text="a", index=0), Turn(text="b", index=1)])
    assert len(log) == 2
    assert isinstance(log[0][0], Progress) and log[0][0].message == "turn 0"
    assert isinstance(log[1][-1], Complete) and log[1][-1].result == "b"


def test_context_accumulates_turns() -> None:
    seen: list[int] = []

    class _CountsTurns:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            seen.append(len(ctx.turns))  # includes the current turn
            return []

    run_behaviour(_CountsTurns(), [Turn(index=0), Turn(index=1), Turn(index=2)])
    assert seen == [1, 2, 3]


def test_behaviour_is_deterministic() -> None:
    turns = [Turn(text="x", index=0), Turn(text="y", index=1)]
    assert run_behaviour(_Echo(), turns) == run_behaviour(_Echo(), turns)


def test_directives_are_frozen() -> None:
    d = Progress("hi")
    try:
        d.message = "changed"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("directives should be immutable")
