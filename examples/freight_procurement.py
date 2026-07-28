"""Dogfood a2a-sandbox on a REAL business problem: cross-company freight procurement.

The business flow (the canonical case where A2A earns its keep — separate companies, so you
genuinely can't just call a function):

    ShipperAgent (us)  ---A2A--->  3 carrier agents at 3 different companies
      1. broadcast the load (2 pallets, LA -> Dallas, deliver Friday)
      2. collect quotes
      3. pick the cheapest VALID quote
      4. book it, and charge the customer that price

The money risk: a carrier agent replies "completed" with a quote that is subtly wrong —
a price as text, a missing currency, a unit error, an expired validity window. Our shipper
books it and invoices the customer off a bad number. No crash, no error log.

We run the SAME procurement twice: once naive (trust the peers) and once guarded
(a2a-sandbox Contract on every quote), and compare business outcomes.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from a2a_sandbox import Contract, MockAgent
from a2a_sandbox.core.behaviour import Complete, NeedInput, Progress
from a2a_sandbox.personas import register

# --- what our business considers a valid carrier quote ----------------------


class CarrierQuote(BaseModel):
    carrier: str
    price: float
    currency: str
    transit_days: int
    valid_until: str  # ISO date; a quote that already expired is worthless


def quote_contract(max_transit_days: int = 3) -> Contract:
    """Our procurement policy, as a machine-checkable contract."""
    today = datetime.now(UTC).date()
    return (
        Contract("carrier freight quote LA->Dallas, 2 pallets")
        .returns(CarrierQuote)
        .require("price_positive", lambda q: q.price > 0)
        .require("price_plausible", lambda q: 200 <= q.price <= 20_000)  # lane sanity band
        # Case-insensitive: a carrier sending "usd" is not a defect. Rejecting it would be
        # a false positive, and an over-strict contract gets switched off.
        .require("usd", lambda q: q.currency.strip().upper() == "USD")
        .require("meets_deadline", lambda q: q.transit_days <= max_transit_days)
        .require(
            "quote_not_expired",
            lambda q: datetime.fromisoformat(q.valid_until).date() >= today,
        )
        .expect_status("completed")
    )


# --- the three carrier agents (each a different company, each with its own flaw) ---

TOMORROW = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
LAST_MONTH = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()


class SwiftFreight:
    """Carrier A — honest and competitive. The one we SHOULD end up booking."""

    def respond(self, turn, ctx):
        return [
            Progress("checking lane availability"),
            Complete(
                result={
                    "carrier": "SwiftFreight",
                    "price": 1420.00,
                    "currency": "USD",
                    "transit_days": 2,
                    "valid_until": TOMORROW,
                }
            ),
        ]


class BudgetHaul:
    """Carrier B — CHEAPEST, and that is the trap: its price is a string ("call for rate").

    Reports completed. A naive shipper sees the lowest 'price' and books it.
    """

    def respond(self, turn, ctx):
        return [
            Complete(
                result={
                    "carrier": "BudgetHaul",
                    "price": "call for rate",
                    "currency": "USD",
                    "transit_days": 2,
                    "valid_until": TOMORROW,
                }
            )
        ]


class RoadRunnerLogistics:
    """Carrier C — a UNIT ERROR: quotes cents-as-dollars (142000.0), plus a stale quote.

    Structurally perfect. Reports completed. Would overcharge the customer 100x.
    """

    def respond(self, turn, ctx):
        return [
            Complete(
                result={
                    "carrier": "RoadRunnerLogistics",
                    "price": 142000.00,  # 1420.00 in cents, mislabeled as dollars
                    "currency": "USD",
                    "transit_days": 2,
                    "valid_until": LAST_MONTH,  # and the quote already expired
                }
            )
        ]


class SlowButHonest:
    """Carrier D — honest, but 6 transit days: misses the Friday deadline. Must be excluded."""

    def respond(self, turn, ctx):
        return [
            Complete(
                result={
                    "carrier": "SlowButHonest",
                    "price": 890.00,  # temptingly cheap!
                    "currency": "USD",
                    "transit_days": 6,
                    "valid_until": TOMORROW,
                }
            )
        ]


class ChattyBroker:
    """Carrier E — a broker whose agent replies in prose instead of structured data."""

    def respond(self, turn, ctx):
        return [
            Complete(
                result={"message": "Happy to help! We can do this lane for around $1,400. Call us."}
            )
        ]


class NeedsAppointment:
    """Carrier F — legitimately needs more info first (multi-turn), then quotes properly."""

    def respond(self, turn, ctx):
        if turn.index == 0:
            return [NeedInput("Is a dock appointment required at the Dallas DC?")]
        return [
            Complete(
                result={
                    "carrier": "NeedsAppointment",
                    "price": 1380.00,
                    "currency": "USD",
                    "transit_days": 3,
                    "valid_until": TOMORROW,
                }
            )
        ]


CARRIERS = [
    ("SwiftFreight", SwiftFreight),
    ("BudgetHaul", BudgetHaul),
    ("RoadRunnerLogistics", RoadRunnerLogistics),
    ("SlowButHonest", SlowButHonest),
    ("ChattyBroker", ChattyBroker),
    ("NeedsAppointment", NeedsAppointment),
]
for _n, _c in CARRIERS:
    register(_n, _c)

LOAD = "Quote 2 pallets, 1,200 lb, LA -> Dallas, pickup Wed, deliver by Friday"


# --- our shipper agent: the thing under test ------------------------------------


async def request_quote(persona: str, *, guard: bool):
    """Ask one carrier agent for a quote over real A2A. Returns (quote_or_None, why)."""
    mock = MockAgent(persona)
    contract = quote_contract() if guard else None
    async with mock.client() as client:
        r = await client.send_message(LOAD, contract=contract)
        # Handle a carrier that needs clarification before it can quote (multi-turn).
        if r.status == "input-required":
            question = r.task.status.message.parts[0].text
            r = await client.reply(
                r.task.id,
                "Yes, dock appointment scheduled 9am",
                context_id=r.task.context_id,
                contract=contract,
            )
            note = f"(answered: {question!r})"
        else:
            note = ""
    if guard and r.contract_violated:
        return None, f"REJECTED: {r.report.failures[0].name} {note}".strip()
    return r.result, f"accepted {note}".strip()


async def run_procurement(*, guard: bool):
    """The business workflow: broadcast, collect, pick cheapest valid, book."""
    label = "GUARDED (a2a-sandbox contracts)" if guard else "NAIVE (trust the peers)"
    print(f"\n--- procurement run: {label} ---")
    offers = []
    for name, _ in CARRIERS:
        quote, why = await request_quote(name, guard=guard)
        if quote is None:
            print(f"  {name:22s} ✗ {why}")
            continue
        price = quote.get("price") if isinstance(quote, dict) else None
        print(f"  {name:22s} ✓ {why:24s} price={price!r}")
        offers.append((name, quote))

    # "Pick the cheapest" — naive code does this the way real code does: sort by price.
    def sort_key(item):
        p = item[1].get("price")
        return p if isinstance(p, (int, float)) else 0.0  # non-numeric sorts first == "cheapest"

    if not offers:
        print("  => NO BOOKABLE QUOTES")
        return None
    winner, quote = sorted(offers, key=sort_key)[0]
    charged = quote.get("price")
    print(f"  => BOOKED {winner} and charged the customer: {charged!r}")
    return {"carrier": winner, "charged": charged, "transit_days": quote.get("transit_days")}


async def main():
    print("=" * 78)
    print("BUSINESS CASE: cross-company freight procurement over A2A")
    print("Load:", LOAD)
    print("Policy: USD, $200-20,000, <=3 transit days, quote must not be expired")
    print("=" * 78)

    naive = await run_procurement(guard=False)
    guarded = await run_procurement(guard=True)

    print("\n" + "=" * 78)
    print("BUSINESS OUTCOME")
    print(f"  naive shipper   -> {naive}")
    print(f"  guarded shipper -> {guarded}")

    # Did the naive run actually do business damage?
    naive_bad = naive is not None and not isinstance(naive["charged"], (int, float))
    guarded_good = (
        guarded is not None
        and isinstance(guarded["charged"], (int, float))
        and guarded["transit_days"] <= 3
        and 200 <= guarded["charged"] <= 20_000
    )
    print()
    if naive_bad:
        print(
            f"  ❌ NAIVE: booked {naive['carrier']} and charged {naive['charged']!r} — "
            "a non-numeric price went to invoicing. Silent partial completion, real money."
        )
    if guarded_good:
        print(
            f"  ✅ GUARDED: booked {guarded['carrier']} at ${guarded['charged']:,.2f}, "
            f"{guarded['transit_days']}-day transit — the correct commercial decision."
        )
    print("=" * 78)
    return naive_bad and guarded_good


if __name__ == "__main__":
    ok = asyncio.run(main())
    print(
        "\nVERDICT:",
        "a2a-sandbox caught the business-damaging failures ✅"
        if ok
        else "did not demonstrate the catch ❌",
    )
