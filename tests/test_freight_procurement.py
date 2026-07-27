"""End-to-end business scenario: cross-company freight procurement over A2A.

Mirrors examples/freight_procurement.py. This is the test that matters most — it asserts the
library changes a real commercial outcome: without contracts a shipper agent books a carrier
whose "completed" quote is unusable; with contracts it books the correct one.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from a2a_sandbox import Contract, MockAgent
from a2a_sandbox.core.behaviour import (
    Complete,
    Directive,
    NeedInput,
    Progress,
    SessionContext,
    Turn,
)
from a2a_sandbox.personas import PersonaFactory, register

FUTURE = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
PAST = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
LOAD = "Quote 2 pallets, 1,200 lb, LA -> Dallas, pickup Wed, deliver by Friday"


class CarrierQuote(BaseModel):
    carrier: str
    price: float
    currency: str
    transit_days: int
    valid_until: str


def quote_contract(max_transit_days: int = 3) -> Contract:
    """Our procurement policy as a machine-checkable contract."""
    today = datetime.now(UTC).date()
    return (
        Contract("carrier freight quote")
        .returns(CarrierQuote)
        .require("price_positive", lambda q: q.price > 0)
        .require("price_plausible", lambda q: 200 <= q.price <= 20_000)
        .require("usd", lambda q: q.currency == "USD")
        .require("meets_deadline", lambda q: q.transit_days <= max_transit_days)
        .require(
            "quote_not_expired",
            lambda q: datetime.fromisoformat(q.valid_until).date() >= today,
        )
        .expect_status("completed")
    )


class FixedQuoteCarrier:
    """A carrier agent that (claims to) complete with a fixed quote payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        return [Progress("checking lane"), Complete(result=self._payload)]


def _quoting_carrier(payload: dict) -> PersonaFactory:
    """A persona factory bound to one quote payload."""
    return lambda **_: FixedQuoteCarrier(payload)


GOOD = {
    "carrier": "SwiftFreight",
    "price": 1420.0,
    "currency": "USD",
    "transit_days": 2,
    "valid_until": FUTURE,
}
# Each of these reports "completed" but is commercially unusable.
BAD_QUOTES = {
    "price_as_text": ({**GOOD, "price": "call for rate"}, "returns"),
    "unit_error": ({**GOOD, "price": 142000.0}, "price_plausible"),
    "expired": ({**GOOD, "valid_until": PAST}, "quote_not_expired"),
    "wrong_currency": ({**GOOD, "currency": "EUR"}, "usd"),
    "too_slow": ({**GOOD, "transit_days": 9}, "meets_deadline"),
    "negative": ({**GOOD, "price": -50.0}, "price_positive"),
}

for _name, (_payload, _rule) in BAD_QUOTES.items():
    register(f"carrier_{_name}", _quoting_carrier(_payload))
register("carrier_good", _quoting_carrier(GOOD))


async def test_valid_quote_passes_the_policy() -> None:
    """No false positives: an honest, compliant carrier is accepted."""
    async with MockAgent("carrier_good").client() as client:
        result = await client.send_message(LOAD, contract=quote_contract())
    assert result.completed
    assert not result.contract_violated


async def test_each_bad_quote_is_caught_by_its_own_rule() -> None:
    """Every commercially-invalid quote is rejected, by the specific rule that should fire."""
    for name, (_payload, expected_rule) in BAD_QUOTES.items():
        async with MockAgent(f"carrier_{name}").client() as client:
            result = await client.send_message(LOAD, contract=quote_contract())
        assert result.status == "completed", f"{name}: carrier claimed success"
        assert result.contract_violated, f"{name}: should have been caught"
        failed = [c.name for c in result.report.failures]
        assert expected_rule in failed, f"{name}: expected {expected_rule}, got {failed}"


async def test_carrier_needing_clarification_still_quotes() -> None:
    """A legitimate multi-turn carrier (needs a dock appointment) completes and passes."""

    class NeedsAppointment:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            if turn.index == 0:
                return [NeedInput("Is a dock appointment required at the Dallas DC?")]
            return [Complete(result={**GOOD, "carrier": "NeedsAppointment", "transit_days": 3})]

    register("carrier_needs_appointment", NeedsAppointment)
    async with MockAgent("carrier_needs_appointment").client() as client:
        first = await client.send_message(LOAD)
        assert first.reached_state("input-required")
        second = await client.reply(
            first.task.id,
            "Yes, appointment scheduled 9am",
            context_id=first.task.context_id,
            contract=quote_contract(),
        )
    assert second.completed
    assert not second.contract_violated


async def test_procurement_picks_cheapest_valid_not_cheapest_claimed() -> None:
    """The business outcome: the unguarded 'cheapest' is a lie; guarded picks the real best."""
    # BudgetHaul looks cheapest to naive code (non-numeric price sorts first) but is invalid.
    register(
        "carrier_budget",
        _quoting_carrier({**GOOD, "carrier": "BudgetHaul", "price": "call for rate"}),
    )
    register("carrier_swift", _quoting_carrier(GOOD))

    offers: list[tuple[str, dict]] = []
    for persona in ("carrier_budget", "carrier_swift"):
        async with MockAgent(persona).client() as client:
            result = await client.send_message(LOAD, contract=quote_contract())
        if not result.contract_violated:
            offers.append((persona, result.result))

    # Only the valid quote survives, so "cheapest valid" is unambiguous and numeric.
    assert [p for p, _ in offers] == ["carrier_swift"]
    assert offers[0][1]["price"] == 1420.0
