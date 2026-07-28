"""Dogfood #3 — space ground segment: scheduling a satellite downlink pass over A2A.

A third failure SHAPE, and the most unforgiving one. Freight was "pick the best of N offers".
Prior auth was "does the answer match the request". This is **units, time systems and reference
frames** — where a response can be perfectly well-formed, internally consistent, and still
describe a completely different physical reality.

    MissionOpsAgent (us, a satellite operator)
      1. --A2A--> GroundStationNetwork agent : schedule an X-band downlink for NORAD 43013
      2. decide: commit the pass to the spacecraft command load, or escalate?

Why this domain is the hard case: this failure class has an infamous track record. NASA lost
the Mars Climate Orbiter (~$327M) because one system produced pound-force-seconds while the
other consumed newton-seconds — nothing crashed, no error was raised, the numbers were simply
in the wrong units. Every failure modelled below is that same shape: the payload validates,
the arithmetic works, and the spacecraft points at empty sky.

Commit a bad pass and the consequences are physical and unrecoverable: the antenna is aimed
somewhere else while the satellite is overhead, so a day of science data is lost with the
downlink, and on a power-negative bus a missed contact can cascade into a safe-hold.

The realities this encodes:
  * GPS time runs ahead of UTC by a whole number of leap seconds (18 s since 2017). A window
    handed over in GPS time and consumed as UTC is silently 18 s early — long enough to miss
    acquisition-of-signal on a fast LEO pass.
  * A TLE (two-line element set) decays in accuracy fast; propagating from an epoch weeks old
    puts a LEO target degrees off. Operators treat TLE age as a hard input constraint.
  * Angles: ground-station software may emit radians or degrees. 0.9 "degrees" that is really
    0.9 radians is 51.6 degrees — plausible-looking, entirely wrong.
  * Longitude sign convention is not universal (east-positive vs west-positive), and getting
    it wrong puts the station on the opposite side of the planet.
  * A negative link margin means the link does not close. The pass can be scheduled, tracked
    and still return nothing.

Run:  uv run python examples/satellite_downlink.py
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from counterpart import Contract, MockAgent
from counterpart.core.behaviour import Complete, Directive, Progress, SessionContext, Turn
from counterpart.personas import register

NOW = datetime.now(UTC)
AOS = (NOW + timedelta(hours=6)).replace(microsecond=0)  # acquisition of signal
LOS = AOS + timedelta(minutes=11)  # loss of signal — a typical LEO pass
GPS_UTC_OFFSET_S = 18  # leap seconds; GPS time is ahead of UTC


@dataclass(frozen=True)
class PassRequest:
    """What mission ops asked the ground station network for."""

    norad_id: int = 43013
    station: str = "SVALBARD-01"
    band: str = "X"
    min_elevation_deg: float = 10.0  # station horizon mask
    required_margin_db: float = 3.0  # link budget margin we will accept
    max_tle_age_days: float = 3.0  # propagation accuracy constraint
    aos_utc: str = AOS.isoformat()
    los_utc: str = LOS.isoformat()


REQUEST = PassRequest()
DATA_VALUE_USD = 240_000.0  # value of one downlink of science data, plus recovery ops


class PassPlan(BaseModel):
    """The ground station network's schedule response."""

    contact_id: str
    norad_id: int
    station: str
    time_system: str  # MUST be UTC — GPS/TAI are silently offset
    aos: str
    los: str
    max_elevation_deg: float
    angle_unit: str  # MUST be deg — radians look plausible and are 57x wrong
    station_lat_deg: float
    station_lon_deg: float
    lon_convention: str  # east-positive; west-positive flips the hemisphere
    reference_frame: str  # ITRF for ground pointing; TEME is a different frame
    link_margin_db: float
    tle_epoch: str
    band: str


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pass_contract(req: PassRequest) -> Contract:
    """What must hold before we commit a pass to the spacecraft command load.

    Note `strict=True`: in this domain a stringified number is a genuine red flag, and we own
    both ends of the interface, so coercion is not a tolerance we want.
    """
    want_aos, want_los = _iso(req.aos_utc), _iso(req.los_utc)
    assert want_aos and want_los

    return (
        Contract(f"downlink pass for NORAD {req.norad_id} at {req.station}")
        .returns(PassPlan, strict=True)
        # --- identity: is this even our spacecraft and our antenna? ---
        .require("correct_spacecraft", lambda p: p.norad_id == req.norad_id)
        .require("correct_station", lambda p: p.station.strip().upper() == req.station)
        .require("correct_band", lambda p: p.band.strip().upper() == req.band)
        # --- the unit/frame/time-system family: right numbers, wrong reality ---
        .require("time_system_is_utc", lambda p: p.time_system.strip().upper() == "UTC")
        .require(
            "angles_in_degrees",
            lambda p: p.angle_unit.strip().lower() in {"deg", "degree", "degrees"},
        )
        .require("longitude_east_positive", lambda p: "east" in p.lon_convention.strip().lower())
        .require("ground_frame_is_itrf", lambda p: p.reference_frame.strip().upper() == "ITRF")
        # --- physical plausibility: catches a radian value masquerading as degrees ---
        .require("elevation_in_range", lambda p: 0.0 <= p.max_elevation_deg <= 90.0)
        .require("station_lat_in_range", lambda p: -90.0 <= p.station_lat_deg <= 90.0)
        .require("station_lon_in_range", lambda p: -180.0 <= p.station_lon_deg <= 180.0)
        # --- the pass has to be usable ---
        .require("clears_horizon_mask", lambda p: p.max_elevation_deg >= req.min_elevation_deg)
        .require("link_closes", lambda p: p.link_margin_db >= req.required_margin_db)
        .require(
            "window_ordered",
            lambda p: (a := _iso(p.aos)) is not None and (b := _iso(p.los)) is not None and b > a,
        )
        .require(
            "window_matches_request",
            # Allow a second of scheduling jitter, but not a leap-second-sized shift.
            lambda p: (a := _iso(p.aos)) is not None and abs((a - want_aos).total_seconds()) <= 1.0,
        )
        .require(
            "tle_fresh_enough",
            lambda p: (
                (e := _iso(p.tle_epoch)) is not None
                and (NOW - e).total_seconds() <= req.max_tle_age_days * 86_400
            ),
        )
        .expect_status("completed")
    )


GOOD_PLAN = {
    "contact_id": "SV01-43013-8821",
    "norad_id": REQUEST.norad_id,
    "station": REQUEST.station,
    "time_system": "UTC",
    "aos": REQUEST.aos_utc,
    "los": REQUEST.los_utc,
    "max_elevation_deg": 41.7,
    "angle_unit": "deg",
    "station_lat_deg": 78.23,  # Svalbard
    "station_lon_deg": 15.41,
    "lon_convention": "east-positive",
    "reference_frame": "ITRF",
    "link_margin_db": 6.2,
    "tle_epoch": (NOW - timedelta(hours=8)).isoformat(),
    "band": "X",
}


def _station(payload: dict) -> type:
    class Fixed:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            return [Progress("propagating TLE, computing visibility"), Complete(result=payload)]

    return Fixed


# --- the failure catalogue: every one reports `completed` -------------------

BAD: dict[str, tuple[dict, str, str]] = {
    "gps_time_not_utc": (
        {
            **GOOD_PLAN,
            "time_system": "GPS",
            "aos": (AOS + timedelta(seconds=GPS_UTC_OFFSET_S)).isoformat(),
            "los": (LOS + timedelta(seconds=GPS_UTC_OFFSET_S)).isoformat(),
        },
        "time system",
        "window handed over in GPS time — 18 s early, misses acquisition on a fast LEO pass",
    ),
    "elevation_in_radians": (
        {**GOOD_PLAN, "angle_unit": "rad", "max_elevation_deg": 0.728},
        "units",
        "0.728 rad is 41.7 deg; consumed as degrees the antenna sits nearly on the horizon",
    ),
    "west_positive_longitude": (
        {**GOOD_PLAN, "lon_convention": "west-positive", "station_lon_deg": -15.41},
        "sign convention",
        "flips the station to the wrong hemisphere",
    ),
    "wrong_reference_frame": (
        {**GOOD_PLAN, "reference_frame": "TEME"},
        "frame",
        "TEME is inertial; ground pointing needs an earth-fixed frame (ITRF)",
    ),
    "stale_tle": (
        {**GOOD_PLAN, "tle_epoch": (NOW - timedelta(days=31)).isoformat()},
        "input staleness",
        "propagating a month-old TLE puts a LEO target degrees off",
    ),
    "negative_link_margin": (
        {**GOOD_PLAN, "link_margin_db": -1.8},
        "physics",
        "pass is trackable but the link never closes — zero bits returned",
    ),
    "below_horizon_mask": (
        {**GOOD_PLAN, "max_elevation_deg": 4.2},
        "geometry",
        "peak elevation under the station's 10 deg mask — no usable contact",
    ),
    "inverted_window": (
        {**GOOD_PLAN, "aos": LOS.isoformat(), "los": AOS.isoformat()},
        "ordering",
        "LOS before AOS — a negative-duration contact",
    ),
    "wrong_spacecraft": (
        {**GOOD_PLAN, "norad_id": 25544},
        "identity",
        "schedules a pass for the ISS instead of our satellite",
    ),
    "impossible_elevation": (
        {**GOOD_PLAN, "max_elevation_deg": 118.0},
        "physics",
        "no elevation above 90 deg exists; a bad frame transform gives this",
    ),
    "stringified_margin": (
        {**GOOD_PLAN, "link_margin_db": "6.2"},
        "type confusion",
        "a number as a string — caught only because this contract is strict=True",
    ),
    "wrong_band": (
        {**GOOD_PLAN, "band": "S"},
        "identity",
        "S-band plan for an X-band request — wrong feed, no downlink",
    ),
}

# Legitimate oddities that MUST be accepted. Several look alarming and are perfectly normal.
GOOD_VARIATIONS: dict[str, tuple[dict, str]] = {
    "negative_longitude": (
        {**GOOD_PLAN, "station_lon_deg": -52.68, "station_lat_deg": -64.24},
        "negative longitude is legal (west of Greenwich) — Rothera, Antarctica",
    ),
    "negative_latitude": (
        {**GOOD_PLAN, "station_lat_deg": -77.85},
        "southern hemisphere station",
    ),
    "exactly_at_mask": (
        {**GOOD_PLAN, "max_elevation_deg": 10.0},
        "peak elevation exactly at the horizon mask — marginal but usable",
    ),
    "zenith_pass": (
        {**GOOD_PLAN, "max_elevation_deg": 90.0},
        "directly overhead — rare, legal, and the best possible geometry",
    ),
    "margin_exactly_required": (
        {**GOOD_PLAN, "link_margin_db": 3.0},
        "margin exactly at the accepted threshold",
    ),
    "brand_new_tle": (
        {**GOOD_PLAN, "tle_epoch": NOW.isoformat()},
        "TLE epoch is right now",
    ),
    "lowercase_units_and_frame": (
        {**GOOD_PLAN, "angle_unit": "DEGREES", "reference_frame": "itrf", "time_system": "utc"},
        "case and spelling variation in enum-ish string fields",
    ),
    "window_crosses_midnight": (
        {
            **GOOD_PLAN,
            "aos": REQUEST.aos_utc,
            "los": (AOS + timedelta(minutes=11)).isoformat(),
        },
        "a pass spanning midnight UTC is ordinary",
    ),
    "offset_zero_timezone": (
        {
            **GOOD_PLAN,
            "aos": AOS.isoformat().replace("+00:00", "Z"),
            "los": LOS.isoformat().replace("+00:00", "Z"),
        },
        "Z suffix instead of +00:00 — same instant, different spelling",
    ),
    "sub_second_jitter": (
        {
            **GOOD_PLAN,
            "aos": (AOS + timedelta(milliseconds=400)).isoformat(),
        },
        "400 ms of scheduling jitter is operationally normal",
    ),
}

for _n, (_p, _c, _d) in BAD.items():
    register(f"gsn_bad_{_n}", _station(_p))
for _n, (_p, _d) in GOOD_VARIATIONS.items():
    register(f"gsn_ok_{_n}", _station(_p))
register("gsn_good", _station(GOOD_PLAN))


async def schedule(persona: str, *, guard: bool) -> tuple[bool, str]:
    """Mission ops asks for a pass and decides whether to commit it to the command load."""
    contract = pass_contract(REQUEST) if guard else None
    async with MockAgent(persona).client() as client:
        r = await client.send_message(
            f"Schedule {REQUEST.band}-band downlink, NORAD {REQUEST.norad_id}, "
            f"{REQUEST.station}, AOS {REQUEST.aos_utc}",
            contract=contract,
        )
    if r.status != "completed":
        return False, f"no plan: task left in {r.status!r}"
    if guard and r.contract_violated:
        return False, r.report.failures[0].name
    # The naive path: a plan came back and the task says completed, so uplink it.
    return True, "committed to command load"


async def main() -> bool:
    print("=" * 96)
    print("BUSINESS CASE: satellite downlink scheduling over A2A (operator <-> ground network)")
    print(
        f"  request : NORAD {REQUEST.norad_id}, {REQUEST.band}-band, {REQUEST.station}, "
        f"mask {REQUEST.min_elevation_deg} deg, margin >= {REQUEST.required_margin_db} dB"
    )
    print(f"  at risk : ${DATA_VALUE_USD:,.0f} of science data per missed contact")
    print("=" * 96)

    print("\n### unusable plans (every one reports 'completed')")
    print(f"  {'ground station response':26s} {'class':17s} {'naive':9s} {'guarded':9s} caught by")
    committed_naive, committed_guarded = [], []
    for name, (_p, cls, desc) in BAD.items():
        naive_ok, _ = await schedule(f"gsn_bad_{name}", guard=False)
        guard_ok, why = await schedule(f"gsn_bad_{name}", guard=True)
        if naive_ok:
            committed_naive.append(name)
        if guard_ok:
            committed_guarded.append(name)
        print(
            f"  {name:26s} {cls:17s} {'COMMIT' if naive_ok else 'hold':9s} "
            f"{'COMMIT' if guard_ok else 'HOLD':9s} {why if not guard_ok else '-'}"
        )
        print(f"      └ {desc}")

    print("\n### legitimate oddities — must be ACCEPTED (no false positives)")
    false_positives = []
    for name, (_p, desc) in GOOD_VARIATIONS.items():
        ok, why = await schedule(f"gsn_ok_{name}", guard=True)
        if not ok:
            false_positives.append((name, why))
        print(f"  {'✅' if ok else '❌ FALSE POSITIVE'} {name:28s} {desc}")
        if not ok:
            print(f"      └ wrongly flagged by: {why}")

    control_ok, control_why = await schedule("gsn_good", guard=True)

    print("\n" + "=" * 96)
    print("OUTCOME")
    print(f"  unusable plans modelled      : {len(BAD)}")
    print(
        f"  naive ops committed anyway   : {len(committed_naive)}"
        f"   (${len(committed_naive) * DATA_VALUE_USD:,.0f} of data at risk)"
    )
    print(f"  guarded ops committed        : {len(committed_guarded)}")
    print(f"  correct plan still committed : {'yes' if control_ok else f'NO ({control_why})'}")
    print(f"  false positives              : {len(false_positives)}")
    print("=" * 96)

    ok = bool(committed_naive) and not committed_guarded and control_ok and not false_positives
    print(
        "\nVERDICT: every unusable pass held, every legal oddity accepted ✅"
        if ok
        else "\nVERDICT: something slipped ❌ — see above"
    )
    return ok


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
