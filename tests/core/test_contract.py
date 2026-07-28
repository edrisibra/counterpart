"""Tests for the contract engine — the silent-partial-completion differentiator.

The star test is ``test_catches_false_success``: a peer reports "completed" but returns a
payload with no real price, and a single assertion catches it.
"""

from pydantic import BaseModel

from counterpart.core import Contract, FailureCategory


class Quote(BaseModel):
    price: float
    currency: str


def test_satisfied_contract() -> None:
    contract = (
        Contract("freight quote")
        .returns(Quote)
        .require("price_positive", lambda q: q.price > 0)
        .expect_status("completed")
    )
    report = contract.verify(
        result={"price": 1250.0, "currency": "USD"}, reported_status="completed"
    )
    assert report.satisfied
    assert not report.contract_violated
    assert bool(report) is True
    assert report.typed_failure is None
    assert report.receipt is not None and report.receipt.price == 1250.0


def test_catches_false_success() -> None:
    """The killer test: peer says 'completed', returns a structurally-wrong payload."""
    contract = (
        Contract("freight quote")
        .returns(Quote)
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .expect_status("completed")
    )
    # Peer reports success but returns prose instead of a quote (the corrupt/incomplete result).
    report = contract.verify(
        result={"message": "Sure, I've prepared your quote!"}, reported_status="completed"
    )
    assert report.contract_violated
    assert report.reported_status == "completed"  # the lie is recorded alongside the failure
    assert report.typed_failure is FailureCategory.STRUCTURE
    assert report.receipt is None  # nothing usable came back


def test_predicate_failure_on_valid_structure() -> None:
    contract = (
        Contract("freight quote").returns(Quote).require("price_positive", lambda q: q.price > 0)
    )
    report = contract.verify(result={"price": -5.0, "currency": "USD"})
    assert report.contract_violated
    assert report.typed_failure is FailureCategory.PREDICATE
    assert report.failures[0].name == "price_positive"


def test_status_mismatch_is_a_violation() -> None:
    contract = Contract("q").returns(Quote).expect_status("completed")
    report = contract.verify(result={"price": 1.0, "currency": "USD"}, reported_status="failed")
    assert report.contract_violated
    assert report.typed_failure is FailureCategory.STATUS


def test_predicates_not_evaluated_when_structure_fails() -> None:
    contract = Contract("q").returns(Quote).require("price_positive", lambda q: q.price > 0)
    report = contract.verify(result={"not": "a quote"})
    # structure check fails; the predicate is reported as not-evaluated (still a failure)
    names = {c.name: c for c in report.checks}
    assert names["returns"].passed is False
    assert names["price_positive"].passed is False
    assert "not evaluated" in names["price_positive"].detail


def test_predicate_that_raises_is_a_failed_check_not_a_crash() -> None:
    contract = (
        Contract("q")
        .returns(Quote)
        .require(
            "explodes",
            lambda q: q.nonexistent_attr,  # type: ignore[attr-defined]
        )
    )
    report = contract.verify(result={"price": 1.0, "currency": "USD"})
    assert report.contract_violated
    assert "raised" in report.failures[0].detail


def test_predicate_returning_non_bool_is_failure() -> None:
    contract = Contract("q").returns(Quote).require("weird", lambda q: q.currency)
    report = contract.verify(result={"price": 1.0, "currency": "USD"})
    # returns the string "USD" (truthy but not True) -> treated as failure, surfaced clearly
    assert report.contract_violated
    assert "returned 'USD'" in report.failures[0].detail


def test_contract_without_model_uses_raw_result() -> None:
    contract = Contract("echo").require("has_ok", lambda r: r.get("ok") is True)
    report = contract.verify(result={"ok": True})
    assert report.satisfied
    assert report.receipt == {"ok": True}


def test_returns_accepts_non_pydantic_shape() -> None:
    contract = Contract("prices").returns(dict[str, float])
    assert contract.verify(result={"usd": 1.0, "eur": 0.9}).satisfied
    assert contract.verify(result={"usd": "not a number"}).contract_violated


def test_summary_is_readable() -> None:
    contract = Contract("freight quote").returns(Quote).expect_status("completed")
    report = contract.verify(result={"nope": 1}, reported_status="completed")
    text = report.summary()
    assert "VIOLATED" in text
    assert "freight quote" in text


# --- type coercion: the trap, and the escape hatch -------------------------


class Fare(BaseModel):
    total: float


def test_lax_mode_coerces_a_stringified_number_and_passes() -> None:
    """Documents a genuine trap (found by an end-to-end chaos test).

    A peer sending `"812.55"` where the model declares `float` is COERCED by pydantic's
    default lax mode, so the contract is satisfied and any `isinstance(x, float)` predicate
    sees a real float. Users must know this, hence the explicit test.
    """
    contract = (
        Contract("fare")
        .returns(Fare)
        .require("is_number", lambda f: isinstance(f.total, (int, float)))
    )
    report = contract.verify(result={"total": "812.55"})
    assert report.satisfied  # coerced, not rejected
    assert report.receipt is not None and report.receipt.total == 812.55


def test_strict_mode_rejects_a_stringified_number() -> None:
    """`strict=True` is the escape hatch for callers who need real type fidelity."""
    contract = Contract("fare").returns(Fare, strict=True)
    report = contract.verify(result={"total": "812.55"})
    assert report.contract_violated
    assert report.typed_failure is FailureCategory.STRUCTURE
    # ...and a genuine number still passes, so this is not just "reject everything".
    assert contract.verify(result={"total": 812.55}).satisfied


def test_strict_mode_also_applies_to_non_pydantic_shapes() -> None:
    lax = Contract("p").returns(dict[str, float])
    strict = Contract("p").returns(dict[str, float], strict=True)
    assert lax.verify(result={"usd": "1.0"}).satisfied
    assert strict.verify(result={"usd": "1.0"}).contract_violated


# --- the short forms: less ceremony for the common case --------------------


def test_field_keywords_build_the_shape_without_a_model_class() -> None:
    """`returns(price=float)` saves defining a BaseModel for a simple check."""
    contract = Contract().returns(price=float, currency=str)
    assert contract.verify(result={"price": 1420.0, "currency": "USD"}).satisfied
    # a missing field is a structure failure, because keyword fields are required
    report = contract.verify(result={"price": 1420.0})
    assert report.contract_violated
    assert report.typed_failure is FailureCategory.STRUCTURE
    # and so is prose instead of the shape
    assert contract.verify(result={"message": "all done!"}).contract_violated


def test_keyword_predicates_avoid_repeating_the_name() -> None:
    contract = (
        Contract()
        .returns(price=float)
        .require(
            price_positive=lambda q: q.price > 0,
            price_plausible=lambda q: q.price < 1_000_000,
        )
    )
    assert contract.verify(result={"price": 100.0}).satisfied
    failures = [c.name for c in contract.verify(result={"price": -1.0}).failures]
    assert failures == ["price_positive"]


def test_positional_require_still_works_for_awkward_names() -> None:
    contract = Contract().returns(price=float).require("price > 0", lambda q: q.price > 0)
    assert contract.verify(result={"price": -1.0}).failures[0].name == "price > 0"


def test_objective_is_optional() -> None:
    assert Contract().objective == "delegated result"
    assert Contract("freight quote").objective == "freight quote"


def test_returns_rejects_shape_and_keywords_together() -> None:
    import pytest as _pytest

    class Quote(BaseModel):
        price: float

    with _pytest.raises(TypeError, match="either a shape or field keywords"):
        Contract().returns(Quote, price=float)


def test_returns_with_nothing_raises_a_helpful_error() -> None:
    import pytest as _pytest

    with _pytest.raises(TypeError, match="needs a shape"):
        Contract().returns()


def test_require_with_nothing_raises() -> None:
    import pytest as _pytest

    with _pytest.raises(TypeError, match="at least one predicate"):
        Contract().require()
