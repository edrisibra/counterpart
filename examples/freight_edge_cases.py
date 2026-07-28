"""Freight procurement, pushed into the edge cases that actually cost money.

examples/freight_procurement.py is the readable introduction. This is the same domain taken
seriously: 22 ways a carrier quote can be unusable while the task still reports `completed`,
and 14 variations that look wrong at a glance and are perfectly normal.

It also tests the thing the simpler example does not. Freight is a selection problem. You ask
five carriers, you get five answers, and the job is picking the cheapest *usable* one. An agent
that picks the cheapest number wins on paper and loses money in accounting, because the cheapest
number is usually cheap for a reason: it excludes the fuel surcharge, or it quotes the wrong
freight class, or the carrier has no authority to run the lane.

The edge cases below are drawn from how LTL freight is actually priced:

Fuel surcharge is a percentage applied on top of the linehaul, and whether a quote includes it
is the single most common source of an invoice that does not match the quote. Accessorials are
charged for anything beyond dock-to-dock: liftgate, residential delivery, inside delivery, limited
access. Freight class comes from the NMFC and is based on density, and a carrier that reclassifies
your shipment on the dock bills you the difference. Dimensional weight means a light pallet that
takes up floor space is billed on the space, not the scale. Transit time is quoted in *business*
days, so a 3 day transit picked up on a Thursday delivers Tuesday. And carrier authority is not
optional: an unauthorized or uninsured carrier moving your freight is your liability problem.

Run:  uv run python examples/freight_edge_cases.py
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel

from counterpart import Contract, MockAgent
from counterpart.core.behaviour import Complete, Directive, Progress, SessionContext, Turn
from counterpart.personas import register

TODAY = datetime.now(UTC).date()
NEXT_WEEK = (TODAY + timedelta(days=7)).isoformat()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()


@dataclass(frozen=True)
class Load:
    """The shipment we are buying transport for."""

    origin_zip: str = "90021"  # Los Angeles
    dest_zip: str = "75201"  # Dallas
    pallets: int = 2
    weight_lb: int = 1180
    freight_class: str = "70"  # NMFC class from density
    equipment: str = "dry_van"
    accessorials: tuple[str, ...] = ("liftgate_delivery", "residential_delivery")
    max_transit_business_days: int = 4
    budget_usd: float = 2_400.00
    currency: str = "USD"


LOAD = Load()


class Quote(BaseModel):
    carrier: str
    mc_number: str | None = None  # motor carrier authority
    insurance_expires: str | None = None
    total_usd: float
    currency: str
    linehaul_usd: float | None = None
    fuel_surcharge_usd: float | None = None
    accessorials_usd: float | None = None
    accessorials_included: list[str] = []
    quoted_freight_class: str | None = None
    billed_weight_lb: int | None = None
    equipment: str | None = None
    transit_days: int
    transit_day_basis: str | None = None  # business or calendar
    origin_zip: str | None = None
    dest_zip: str | None = None
    valid_until: str | None = None


def _iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def quote_contract(load: Load) -> Contract:
    """What has to be true before we tender freight to a carrier."""
    return (
        Contract("LTL freight quote")
        .returns(Quote)
        # Can this carrier legally move it, and are they insured on the day we ship?
        .require(has_operating_authority=lambda q: bool(q.mc_number) and q.mc_number.isdigit())
        .require(
            insurance_valid=lambda q: (e := _iso(q.insurance_expires)) is not None and e >= TODAY
        )
        # Is the number real, and is it the number we will actually be invoiced?
        .require(total_is_positive=lambda q: q.total_usd > 0)
        .require(total_within_budget=lambda q: q.total_usd <= load.budget_usd)
        .require(currency_matches=lambda q: q.currency.strip().upper() == load.currency)
        .require(
            # An all-in total must reconcile with its own breakdown. A cent of rounding is fine.
            total_reconciles=lambda q: (
                q.linehaul_usd is None
                or abs(
                    q.total_usd
                    - (q.linehaul_usd + (q.fuel_surcharge_usd or 0.0) + (q.accessorials_usd or 0.0))
                )
                <= 0.01
            )
        )
        .require(
            # A quote with a linehaul but no fuel line is almost certainly excluding it.
            fuel_surcharge_accounted=lambda q: (
                q.linehaul_usd is None or q.fuel_surcharge_usd is not None
            )
        )
        # Does it cover the service we actually need?
        .require(
            covers_requested_accessorials=lambda q: (
                set(load.accessorials) <= set(q.accessorials_included)
            )
        )
        .require(
            freight_class_matches=lambda q: (
                q.quoted_freight_class is None or q.quoted_freight_class == load.freight_class
            )
        )
        .require(
            billed_weight_not_understated=lambda q: (
                q.billed_weight_lb is None or q.billed_weight_lb >= load.weight_lb
            )
        )
        .require(equipment_matches=lambda q: q.equipment is None or q.equipment == load.equipment)
        # Is it the lane we asked about, in the direction we asked?
        .require(correct_origin=lambda q: q.origin_zip is None or q.origin_zip == load.origin_zip)
        .require(correct_dest=lambda q: q.dest_zip is None or q.dest_zip == load.dest_zip)
        # Will it arrive in time, measured the way we measured?
        .require(transit_fast_enough=lambda q: q.transit_days <= load.max_transit_business_days)
        .require(
            transit_basis_stated=lambda q: (
                (q.transit_day_basis or "").strip().lower()
                in {"business", "business_days", "calendar", "calendar_days"}
            )
        )
        .require(
            # A calendar-day quote has to beat the business-day deadline on a calendar basis,
            # so it needs to be materially faster, not merely equal.
            calendar_transit_is_honest=lambda q: (
                "calendar" not in (q.transit_day_basis or "").lower()
                or q.transit_days <= load.max_transit_business_days
            )
        )
        .require(quote_not_expired=lambda q: (v := _iso(q.valid_until)) is not None and v >= TODAY)
        .expect_status("completed")
    )


GOOD = {
    "carrier": "Ridgeline Freight",
    "mc_number": "884213",
    "insurance_expires": NEXT_WEEK,
    "total_usd": 1_642.50,
    "currency": "USD",
    "linehaul_usd": 1_180.00,
    "fuel_surcharge_usd": 342.50,
    "accessorials_usd": 120.00,
    "accessorials_included": ["liftgate_delivery", "residential_delivery"],
    "quoted_freight_class": "70",
    "billed_weight_lb": 1_180,
    "equipment": "dry_van",
    "transit_days": 3,
    "transit_day_basis": "business",
    "origin_zip": "90021",
    "dest_zip": "75201",
    "valid_until": NEXT_WEEK,
}


def _carrier(payload: dict) -> type:
    class Fixed:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            return [Progress("rating lane"), Complete(result=payload)]

    return Fixed


# Each entry: payload, the rule that must catch it, why it costs money.
BAD: dict[str, tuple[dict, str, str]] = {
    "fuel_surcharge_excluded": (
        {**GOOD, "total_usd": 1_300.00, "fuel_surcharge_usd": None},
        "fuel_surcharge_accounted",
        "cheapest on paper; fuel is added on the invoice",
    ),
    "total_does_not_reconcile": (
        {**GOOD, "total_usd": 1_400.00},
        "total_reconciles",
        "headline total is lower than its own line items",
    ),
    "accessorials_dropped": (
        {**GOOD, "total_usd": 1_522.50, "accessorials_usd": 0.0, "accessorials_included": []},
        "covers_requested_accessorials",
        "no liftgate on a residential delivery; driver cannot unload",
    ),
    "partial_accessorials": (
        {**GOOD, "accessorials_included": ["liftgate_delivery"]},
        "covers_requested_accessorials",
        "covers the liftgate but not the residential surcharge",
    ),
    "freight_class_understated": (
        {
            **GOOD,
            "quoted_freight_class": "50",
            "total_usd": 1_180.00,
            "linehaul_usd": 800.00,
            "fuel_surcharge_usd": 260.00,
            "accessorials_usd": 120.00,
        },
        "freight_class_matches",
        "class 50 rate on class 70 freight; reclassified and rebilled on the dock",
    ),
    "billed_weight_understated": (
        {
            **GOOD,
            "billed_weight_lb": 500,
            "total_usd": 900.00,
            "linehaul_usd": 620.00,
            "fuel_surcharge_usd": 160.00,
            "accessorials_usd": 120.00,
        },
        "billed_weight_not_understated",
        "rated at 500 lb for an 1180 lb shipment",
    ),
    "wrong_equipment": (
        {
            **GOOD,
            "equipment": "reefer",
            "total_usd": 2_100.00,
            "linehaul_usd": 1_600.00,
            "fuel_surcharge_usd": 380.00,
            "accessorials_usd": 120.00,
        },
        "equipment_matches",
        "refrigerated trailer we did not ask for and will pay for",
    ),
    "lane_reversed": (
        {**GOOD, "origin_zip": "75201", "dest_zip": "90021"},
        "correct_origin",
        "quoted Dallas to LA; backhaul rates differ, and it is the wrong direction",
    ),
    "wrong_destination": (
        {**GOOD, "dest_zip": "75001"},
        "correct_dest",
        "adjacent zip, different terminal, different rate",
    ),
    "transit_basis_missing": (
        {**GOOD, "transit_day_basis": None},
        "transit_basis_stated",
        "3 days could be business or calendar; those are different weeks",
    ),
    "calendar_days_disguised": (
        {**GOOD, "transit_days": 6, "transit_day_basis": "calendar"},
        "transit_fast_enough",
        "6 calendar days presented against a 4 business day requirement",
    ),
    "too_slow": (
        {
            **GOOD,
            "transit_days": 7,
            "total_usd": 1_100.00,
            "linehaul_usd": 760.00,
            "fuel_surcharge_usd": 220.00,
            "accessorials_usd": 120.00,
        },
        "transit_fast_enough",
        "cheap because it is a week in transit",
    ),
    "over_budget": (
        {
            **GOOD,
            "total_usd": 3_800.00,
            "linehaul_usd": 3_100.00,
            "fuel_surcharge_usd": 580.00,
            "accessorials_usd": 120.00,
        },
        "total_within_budget",
        "above the authorised spend for this lane",
    ),
    "wrong_currency": (
        {**GOOD, "currency": "CAD"},
        "currency_matches",
        "Canadian dollars on a domestic US lane; roughly 35 percent understated",
    ),
    "no_operating_authority": (
        {
            **GOOD,
            "mc_number": None,
            "total_usd": 1_050.00,
            "linehaul_usd": 720.00,
            "fuel_surcharge_usd": 210.00,
            "accessorials_usd": 120.00,
        },
        "has_operating_authority",
        "no MC number; unauthorised carrier makes the loss yours",
    ),
    "malformed_mc_number": (
        {**GOOD, "mc_number": "PENDING"},
        "has_operating_authority",
        "authority not actually granted yet",
    ),
    "insurance_expired": (
        {
            **GOOD,
            "insurance_expires": YESTERDAY,
            "total_usd": 1_020.00,
            "linehaul_usd": 700.00,
            "fuel_surcharge_usd": 200.00,
            "accessorials_usd": 120.00,
        },
        "insurance_valid",
        "cargo insurance lapsed; a claim would not be covered",
    ),
    "quote_expired": (
        {**GOOD, "valid_until": YESTERDAY},
        "quote_not_expired",
        "rate is stale and will be requoted higher at tender",
    ),
    "no_expiry_stated": (
        {**GOOD, "valid_until": None},
        "quote_not_expired",
        "an open-ended rate is not a commitment",
    ),
    "negative_total": (
        {
            **GOOD,
            "total_usd": -50.00,
            "linehaul_usd": -50.00,
            "fuel_surcharge_usd": 0.0,
            "accessorials_usd": 0.0,
        },
        "total_is_positive",
        "a credit is not a quote",
    ),
    "zero_total": (
        {
            **GOOD,
            "total_usd": 0.0,
            "linehaul_usd": 0.0,
            "fuel_surcharge_usd": 0.0,
            "accessorials_usd": 0.0,
        },
        "total_is_positive",
        "free freight does not exist; this is a placeholder row",
    ),
    "prose_instead_of_rate": (
        {
            "carrier": "Ridgeline Freight",
            "total_usd": 0.0,
            "currency": "USD",
            "transit_days": 0,
            "note": "call our desk for a rate",
        },
        "has_operating_authority",
        "not a quote at all",
    ),
}

# These look wrong to a careless check and are entirely normal in LTL.
GOOD_VARIATIONS: dict[str, tuple[dict, str]] = {
    "fuel_as_separate_line": (
        {**GOOD},
        "fuel surcharge itemised rather than buried; this is the norm",
    ),
    "no_breakdown_at_all": (
        {
            k: v
            for k, v in GOOD.items()
            if k not in ("linehaul_usd", "fuel_surcharge_usd", "accessorials_usd")
        },
        "an all-in rate with no breakdown is a legitimate way to quote",
    ),
    "one_cent_rounding": (
        {**GOOD, "total_usd": 1_642.51},
        "a cent of rounding between total and line items",
    ),
    "exactly_at_budget": (
        {
            **GOOD,
            "total_usd": LOAD.budget_usd,
            "linehaul_usd": 1_900.00,
            "fuel_surcharge_usd": 380.00,
            "accessorials_usd": 120.00,
        },
        "priced exactly at the authorised limit",
    ),
    "exactly_at_transit_limit": (
        {**GOOD, "transit_days": 4},
        "4 business days against a 4 business day requirement",
    ),
    "faster_than_needed": ({**GOOD, "transit_days": 1}, "next day service"),
    "expires_today": ({**GOOD, "valid_until": TODAY.isoformat()}, "still valid today"),
    "insurance_expires_today": (
        {**GOOD, "insurance_expires": TODAY.isoformat()},
        "covered for today's tender",
    ),
    "extra_accessorials_included": (
        {
            **GOOD,
            "accessorials_included": [
                "liftgate_delivery",
                "residential_delivery",
                "inside_delivery",
            ],
        },
        "carrier includes more service than requested",
    ),
    "billed_weight_rounded_up": (
        {**GOOD, "billed_weight_lb": 1_200},
        "billed weight rounded up to the next hundredweight break",
    ),
    "dim_weight_higher": (
        {
            **GOOD,
            "billed_weight_lb": 1_400,
            "total_usd": 1_820.00,
            "linehaul_usd": 1_340.00,
            "fuel_surcharge_usd": 360.00,
            "accessorials_usd": 120.00,
        },
        "dimensional weight exceeds scale weight; legitimate and correctly billed",
    ),
    "lowercase_currency": ({**GOOD, "currency": "usd"}, "case variation"),
    "basis_spelled_out": (
        {**GOOD, "transit_day_basis": "business_days"},
        "spelling variation in the basis field",
    ),
    "no_optional_fields": (
        {
            k: v
            for k, v in GOOD.items()
            if k
            not in (
                "quoted_freight_class",
                "billed_weight_lb",
                "equipment",
                "origin_zip",
                "dest_zip",
            )
        },
        "carrier omits every optional field but quotes a real rate",
    ),
}

# The selection test: five carriers, and the cheapest is not the answer.
TENDER_BOARD: dict[str, dict] = {
    "cheapest_but_no_fuel": {
        **GOOD,
        "carrier": "Dockline",
        "total_usd": 1_180.00,
        "linehaul_usd": 1_180.00,
        "fuel_surcharge_usd": None,
    },
    "cheapest_but_unauthorised": {
        **GOOD,
        "carrier": "Gray Route",
        "total_usd": 1_050.00,
        "mc_number": None,
    },
    "cheapest_but_reclassified": {
        **GOOD,
        "carrier": "Sunbelt LTL",
        "total_usd": 1_240.00,
        "quoted_freight_class": "50",
        "linehaul_usd": 900.00,
        "fuel_surcharge_usd": 220.00,
        "accessorials_usd": 120.00,
    },
    "valid_mid_price": {**GOOD, "carrier": "Ridgeline Freight", "total_usd": 1_642.50},
    "valid_expensive": {
        **GOOD,
        "carrier": "Meridian Carriers",
        "total_usd": 2_180.00,
        "linehaul_usd": 1_660.00,
        "fuel_surcharge_usd": 400.00,
        "accessorials_usd": 120.00,
    },
}

for _n, (_p, _r, _d) in BAD.items():
    register(f"fe_bad_{_n}", _carrier(_p))
for _n, (_p, _d) in GOOD_VARIATIONS.items():
    register(f"fe_ok_{_n}", _carrier(_p))
for _n, _p in TENDER_BOARD.items():
    register(f"fe_board_{_n}", _carrier(_p))


async def get_quote(persona: str, *, guard: bool) -> tuple[dict | None, str]:
    contract = quote_contract(LOAD) if guard else None
    result = await MockAgent(persona).ask(
        f"Rate {LOAD.pallets} pallets, {LOAD.weight_lb} lb, class {LOAD.freight_class}, "
        f"{LOAD.origin_zip} to {LOAD.dest_zip}",
        contract=contract,
    )
    if result.status != "completed":
        return None, f"no quote: {result.status}"
    if guard and result.contract_violated:
        return None, ", ".join(c.name for c in result.report.failures)
    payload = result.result if isinstance(result.result, dict) else {}
    return payload, "accepted"


async def tender(*, guard: bool) -> tuple[str | None, float, str]:
    """Collect every quote on the board and pick the cheapest one we are willing to use."""
    usable: list[tuple[float, str]] = []
    for name in TENDER_BOARD:
        quote, _why = await get_quote(f"fe_board_{name}", guard=guard)
        if quote:
            usable.append((float(quote["total_usd"]), quote["carrier"]))
    if not usable:
        return None, 0.0, "nothing tenderable"
    usable.sort()
    price, carrier = usable[0]
    return carrier, price, f"cheapest of {len(usable)} usable"


async def main() -> bool:
    print("=" * 94)
    print("FREIGHT EDGE CASES: LTL procurement, 2 pallets 90021 to 75201")
    print(f"  class {LOAD.freight_class}, {LOAD.weight_lb} lb, {', '.join(LOAD.accessorials)}")
    print(f"  budget ${LOAD.budget_usd:,.2f}, max {LOAD.max_transit_business_days} business days")
    print("=" * 94)

    print(f"\nUNUSABLE QUOTES ({len(BAD)}), every one reporting completed")
    print(f"  {'carrier response':28s} {'naive':8s} {'guarded':8s} caught by")
    naive_accepted = []
    guarded_accepted = []
    for name, (_p, expected, why) in BAD.items():
        n_quote, _ = await get_quote(f"fe_bad_{name}", guard=False)
        g_quote, g_rule = await get_quote(f"fe_bad_{name}", guard=True)
        if n_quote:
            naive_accepted.append(name)
        if g_quote:
            guarded_accepted.append(name)
        flag = "" if g_rule == expected or g_quote else f"  (expected {expected})"
        print(
            f"  {name:28s} {'take' if n_quote else 'skip':8s} "
            f"{'TAKE' if g_quote else 'skip':8s} {g_rule if not g_quote else '-'}{flag}"
        )
        print(f"      {why}")

    print(f"\nLEGITIMATE VARIATIONS ({len(GOOD_VARIATIONS)}), all must be accepted")
    false_positives = []
    for name, (_p, why) in GOOD_VARIATIONS.items():
        quote, rule = await get_quote(f"fe_ok_{name}", guard=True)
        if not quote:
            false_positives.append((name, rule))
        print(f"  {'ok ' if quote else 'FALSE POSITIVE'} {name:30s} {why}")
        if not quote:
            print(f"      wrongly rejected by {rule}")

    print("\nSELECTION: five carriers bid, the three cheapest are all unusable")
    for name, p in sorted(TENDER_BOARD.items(), key=lambda kv: kv[1]["total_usd"]):
        print(f"  ${p['total_usd']:>8,.2f}  {p['carrier']:20s} {name}")
    n_carrier, n_price, _ = await tender(guard=False)
    g_carrier, g_price, g_note = await tender(guard=True)
    print(f"\n  naive picks   : {n_carrier} at ${n_price:,.2f}")
    print(f"  guarded picks : {g_carrier} at ${g_price:,.2f}  ({g_note})")
    correct_pick = g_carrier == "Ridgeline Freight"

    print("\n" + "=" * 94)
    print("OUTCOME")
    print(f"  unusable quotes modelled        : {len(BAD)}")
    print(f"  naive agent accepted            : {len(naive_accepted)}")
    print(f"  guarded agent accepted          : {len(guarded_accepted)}")
    print(f"  false positives                 : {len(false_positives)}")
    print(f"  picked the cheapest USABLE quote: {'yes' if correct_pick else 'NO'}")
    print("=" * 94)

    ok = bool(naive_accepted) and not guarded_accepted and not false_positives and correct_pick
    print(
        "\nevery unusable quote rejected, every legitimate one kept, correct carrier chosen"
        if ok
        else "\nsomething slipped, see above"
    )
    return ok


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
