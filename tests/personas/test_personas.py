"""Persona behaviour tests, plus the in-process flagship: false_success caught by a Contract.

These prove the personas' logic with no protocol/server involved — the protocol-agnostic
core delivering value on its own (the hedge against thin A2A adoption).
"""

from pydantic import BaseModel

from a2a_sandbox.core import Complete, Contract, Drop, NeedInput, Progress, Turn, run_behaviour
from a2a_sandbox.personas import BUILTIN_PERSONAS, available, get_persona


class Quote(BaseModel):
    price: float
    currency: str


def _first_turn() -> Turn:
    return Turn(text="Quote 2 pallets LA->Dallas", index=0)


def test_registry_lists_v0_personas() -> None:
    # BUILTIN_PERSONAS is exact; available() is a superset because registering custom
    # personas is a designed feature (other tests/examples add their own).
    assert set(BUILTIN_PERSONAS) == {
        "cooperative",
        "clarifier",
        "false_success",
        "resource_abuse",
        "flaky",
        "over_sharing",
    }
    assert set(BUILTIN_PERSONAS) <= set(available())


def test_unknown_persona_is_a_clear_error() -> None:
    try:
        get_persona("hostile_mastermind")
    except KeyError as exc:
        assert "unknown persona" in str(exc)
        assert "false_success" in str(exc)  # lists what's available
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_cooperative_works_then_completes() -> None:
    persona = get_persona("cooperative", result={"price": 1200.0, "currency": "USD"})
    [directives] = run_behaviour(persona, [_first_turn()])
    assert isinstance(directives[0], Progress)
    assert isinstance(directives[-1], Complete)
    assert directives[-1].result == {"price": 1200.0, "currency": "USD"}


def test_clarifier_asks_once_then_completes() -> None:
    persona = get_persona("clarifier", question="Deliver by when?")
    turns = [_first_turn(), Turn(text="Friday", index=1)]
    first, second = run_behaviour(persona, turns)
    assert isinstance(first[0], NeedInput)
    assert first[0].question == "Deliver by when?"
    assert isinstance(second[-1], Complete)


def test_false_success_completes_with_garbage_by_default() -> None:
    persona = get_persona("false_success")
    [directives] = run_behaviour(persona, [_first_turn()])
    assert len(directives) == 1
    assert isinstance(directives[0], Complete)
    # It reports done, but the result has no usable structure.
    assert directives[0].result == {"message": "All done!"}


def test_flaky_drops_then_recovers() -> None:
    persona = get_persona("flaky", drops=1)
    first, second = run_behaviour(persona, [_first_turn(), Turn(index=1)])
    assert any(isinstance(d, Drop) for d in first)
    assert isinstance(second[-1], Complete)


def test_resource_abuse_never_completes() -> None:
    persona = get_persona("resource_abuse", forever=True)
    [directives] = run_behaviour(persona, [_first_turn()])
    assert not any(isinstance(d, Complete) for d in directives)
    assert directives  # it does emit progress + wait, just never finishes


def test_over_sharing_asks_for_too_much() -> None:
    persona = get_persona("over_sharing")
    [directives] = run_behaviour(persona, [_first_turn()])
    assert isinstance(directives[0], NeedInput)
    assert "keys" in directives[0].question.lower() or "database" in directives[0].question.lower()


# --- the flagship, end-to-end in-process -----------------------------------


def _quote_contract() -> Contract:
    return (
        Contract("freight quote LA->Dallas")
        .returns(Quote)
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .require("price_positive", lambda q: q.price > 0)
        .expect_status("completed")
    )


def _final_result(persona_name: str, **cfg: object) -> object:
    """Run a persona to completion in-process and return the final delivered result."""
    persona = get_persona(persona_name, **cfg)
    result: object = None
    for directives in run_behaviour(persona, [_first_turn(), Turn(index=1)]):
        for d in directives:
            if isinstance(d, Complete):
                result = d.result
    return result


def test_flagship_contract_catches_false_success_in_process() -> None:
    """THE killer test, no server: false_success says done, contract catches the garbage."""
    result = _final_result("false_success")
    report = _quote_contract().verify(result=result, reported_status="completed")
    assert report.contract_violated
    assert report.reported_status == "completed"  # the lie, recorded next to the failure


def test_flagship_contract_passes_for_cooperative() -> None:
    """Control: a correct counterparty satisfies the same contract."""
    result = _final_result("cooperative", result={"price": 1450.0, "currency": "USD"})
    report = _quote_contract().verify(result=result, reported_status="completed")
    assert report.satisfied
