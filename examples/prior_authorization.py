"""Dogfood #2 — healthcare revenue cycle: prior authorization over A2A.

A different vertical and a different FAILURE SHAPE from examples/freight_procurement.py.
Freight was "pick the cheapest valid quote from N competing offers." This is a *pipeline with
cross-field consistency*: a single authoritative payer answers, and the danger is that its
answer doesn't actually match what we asked for.

The business flow (mandatory cross-org — the provider and payer are different companies, so
A2A is genuinely warranted, not overkill):

    ProviderRevenueAgent (us, a clinic)
      1. --A2A--> payer ELIGIBILITY agent      : is coverage active on the service date?
      2. --A2A--> payer UTILIZATION MGMT agent : authorize CPT 29881, right knee, Friday
      3. decide: safe to schedule, or escalate to a human?

The money and the risk: an outpatient knee arthroscopy runs several thousand dollars. If we
schedule on a bad authorization the claim is denied and the provider eats the cost, or the
patient gets a surprise bill (a No Surprises Act problem). Nothing crashes — the payer agent
reports the task "completed" every single time.

WHY THIS IS THE HARD CASE: in A2A, `completed` means *the agent finished its work*, not
*you got what you asked for*. A payer agent that cleanly returns "DENIED", or approves a
cheaper procedure code, or authorizes the LEFT knee when you asked about the RIGHT, has
completed successfully at the protocol level. Conformance testing cannot see any of this.
Only a contract on the returned content can.

Run:  uv run python examples/prior_authorization.py
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
from a2a_sandbox.personas import register

# --- dates -----------------------------------------------------------------

TODAY = datetime.now(UTC).date()
SERVICE_DATE = (TODAY + timedelta(days=4)).isoformat()  # the scheduled procedure ("Friday")
LAST_MONTH = (TODAY - timedelta(days=30)).isoformat()
NEXT_MONTH = (TODAY + timedelta(days=35)).isoformat()
IN_A_YEAR = (TODAY + timedelta(days=365)).isoformat()

# --- what we are asking for ------------------------------------------------


@dataclass(frozen=True)
class AuthRequest:
    """The prior-auth request our clinic submits. Everything here must come back matching."""

    member_id: str = "W123456789"
    patient_last_name: str = "OKONKWO"
    cpt_code: str = "29881"  # arthroscopy, knee, surgical; with meniscectomy
    icd10_code: str = "M23.221"  # derangement, posterior horn medial meniscus, RIGHT knee
    laterality: str = "RT"  # right knee — wrong side is a patient-safety event
    units: int = 1
    place_of_service: str = "22"  # on-campus outpatient hospital (not office/ASC)
    facility_npi: str = "1234567893"
    service_date: str = SERVICE_DATE


REQUEST = AuthRequest()
ALLOWED_AMOUNT_USD = 6_800.00  # what the provider bills/collects if the claim is paid


# --- the payer's response shapes -------------------------------------------


class Eligibility(BaseModel):
    member_id: str
    plan_name: str
    coverage_active: bool
    coverage_start: str
    coverage_end: str | None = None
    deductible_remaining_cents: int


class AuthDetermination(BaseModel):
    authorization_number: str
    determination: str  # APPROVED | DENIED | PENDED
    member_id: str
    cpt_codes: list[str]
    laterality: str | None = None
    units_approved: int
    place_of_service: str
    effective_date: str
    expiration_date: str
    patient_responsibility_cents: int


# --- our clinic's policy, as machine-checkable contracts -------------------

_PLACEHOLDERS = {"", "PENDING", "PENDING-REVIEW", "N/A", "NA", "TBD", "UNKNOWN", "0", "00000000"}


def _auth_number_usable(number: str) -> bool:
    """A real auth number: no placeholder text, no stray whitespace (breaks exact-match
    lookups in the claim scrubber), plausible length."""
    if number != number.strip():
        return False  # trailing/leading whitespace silently fails downstream matching
    if number.strip().upper() in _PLACEHOLDERS:
        return False
    return len(number) >= 6 and any(c.isdigit() for c in number)


def eligibility_contract(req: AuthRequest) -> Contract:
    """Coverage must be active *on the service date*, for the member we asked about."""
    return (
        Contract("payer eligibility check")
        .returns(Eligibility)
        .require("member_matches", lambda e: e.member_id.strip() == req.member_id)
        .require("coverage_active", lambda e: e.coverage_active is True)
        .require(
            "active_on_service_date",
            # A retroactive termination is the classic trap: "active" today, but the plan
            # ended before the date we intend to render service.
            lambda e: e.coverage_end is None or e.coverage_end >= req.service_date,
        )
        .require("coverage_started", lambda e: e.coverage_start <= req.service_date)
        .require(
            "deductible_plausible",
            lambda e: 0 <= e.deductible_remaining_cents <= 5_000_000,
        )
        .expect_status("completed")
    )


def auth_contract(req: AuthRequest) -> Contract:
    """The authorization must actually authorize *what we asked for*, for *our patient*,
    on *our date*. Every rule here maps to a documented real-world denial cause."""
    return (
        Contract(f"prior auth for CPT {req.cpt_code} {req.laterality} on {req.service_date}")
        .returns(AuthDetermination)
        # 1. Protocol success is not business approval.
        .require("determination_approved", lambda a: a.determination.upper() == "APPROVED")
        # 2. The auth number has to survive the claim scrubber.
        .require("auth_number_usable", lambda a: _auth_number_usable(a.authorization_number))
        # 3. It has to be OUR patient (identity / record-overlay errors).
        .require("member_matches", lambda a: a.member_id.strip() == req.member_id)
        # 4. It has to cover the procedure we requested (silent downgrade to a cheaper code).
        .require("covers_requested_cpt", lambda a: req.cpt_code in a.cpt_codes)
        # 5. Correct side. Wrong laterality is a wrong-site-surgery and denial risk.
        .require("laterality_matches", lambda a: (a.laterality or "").upper() == req.laterality)
        # 6. Enough units/visits (partial approval dressed up as approval).
        .require("units_sufficient", lambda a: a.units_approved >= req.units)
        # 7. Right setting — an outpatient-hospital request downgraded to office won't pay.
        .require("place_of_service_matches", lambda a: a.place_of_service == req.place_of_service)
        # 8. The auth window must cover the service date.
        .require(
            "active_on_service_date",
            lambda a: a.effective_date <= req.service_date <= a.expiration_date,
        )
        # 9. Patient responsibility must be sane — a cents/dollars mixup misquotes the patient
        #    under the No Surprises Act.
        .require(
            "patient_responsibility_plausible",
            lambda a: 0 <= a.patient_responsibility_cents <= 2_000_000,
        )
        .expect_status("completed")
    )


# --- payer agents: each models one documented real-world failure -----------

CLEAN_ELIGIBILITY = {
    "member_id": REQUEST.member_id,
    "plan_name": "Harbor PPO Gold",
    "coverage_active": True,
    "coverage_start": "2026-01-01",
    "coverage_end": None,
    "deductible_remaining_cents": 45_000,  # $450 left on the deductible
}

CLEAN_AUTH = {
    "authorization_number": "AUTH-2026-8814720",
    "determination": "APPROVED",
    "member_id": REQUEST.member_id,
    "cpt_codes": [REQUEST.cpt_code],
    "laterality": REQUEST.laterality,
    "units_approved": REQUEST.units,
    "place_of_service": REQUEST.place_of_service,
    "effective_date": TODAY.isoformat(),
    "expiration_date": IN_A_YEAR,
    "patient_responsibility_cents": 45_000,
}


def _payer(payload: object, *, progress: str = "adjudicating request") -> type:
    """Build a payer agent persona that completes with a fixed payload."""

    class FixedPayer:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            return [Progress(progress), Complete(result=payload)]

    return FixedPayer


# ---- eligibility-stage payers ----
ELIGIBILITY_PAYERS: dict[str, tuple[type, str, str]] = {
    "elig_clean": (_payer(CLEAN_ELIGIBILITY), "common", "active coverage, correct member"),
    "elig_retro_termed": (
        _payer({**CLEAN_ELIGIBILITY, "coverage_active": True, "coverage_end": LAST_MONTH}),
        "common",
        "says active, but the plan terminated last month (retroactive term)",
    ),
    "elig_wrong_member": (
        _payer({**CLEAN_ELIGIBILITY, "member_id": "W999888777"}),
        "long-tail",
        "returns another member's coverage (record overlay / mismatch)",
    ),
    "elig_future_coverage": (
        _payer({**CLEAN_ELIGIBILITY, "coverage_start": NEXT_MONTH}),
        "long-tail",
        "coverage does not begin until after the service date",
    ),
    "elig_absurd_deductible": (
        _payer({**CLEAN_ELIGIBILITY, "deductible_remaining_cents": -1}),
        "long-tail",
        "negative deductible remaining",
    ),
}

# ---- prior-auth-stage payers ----
AUTH_PAYERS: dict[str, tuple[type, str, str]] = {
    "auth_clean": (_payer(CLEAN_AUTH), "common", "clean approval for exactly what we asked"),
    "auth_pended_as_approved": (
        _payer({**CLEAN_AUTH, "authorization_number": "PENDING-REVIEW"}),
        "common",
        "APPROVED with a placeholder auth number that the claim will reject",
    ),
    "auth_denied": (
        _payer({**CLEAN_AUTH, "determination": "DENIED", "authorization_number": "DENY-55021"}),
        "common",
        "cleanly DENIED — the agent still 'completed' successfully",
    ),
    "auth_cpt_downgrade": (
        _payer({**CLEAN_AUTH, "cpt_codes": ["29870"]}),
        "common",
        "approves diagnostic scope 29870 instead of the requested surgical 29881",
    ),
    "auth_short_units": (
        _payer({**CLEAN_AUTH, "units_approved": 0}),
        "common",
        "APPROVED but zero units authorized (partial approval as approval)",
    ),
    "auth_pos_downgrade": (
        _payer({**CLEAN_AUTH, "place_of_service": "11"}),
        "common",
        "downgrades outpatient hospital (22) to office (11)",
    ),
    "auth_expired_window": (
        _payer({**CLEAN_AUTH, "effective_date": NEXT_MONTH, "expiration_date": IN_A_YEAR}),
        "common",
        "auth window starts after the scheduled service date",
    ),
    "auth_wrong_laterality": (
        _payer({**CLEAN_AUTH, "laterality": "LT"}),
        "long-tail",
        "authorizes the LEFT knee for a RIGHT knee request (wrong-site risk)",
    ),
    "auth_wrong_member": (
        _payer({**CLEAN_AUTH, "member_id": "W555000111"}),
        "long-tail",
        "authorization issued against a different member's ID",
    ),
    "auth_whitespace_id": (
        _payer({**CLEAN_AUTH, "authorization_number": "AUTH-2026-8814720 "}),
        "long-tail",
        "auth number has a trailing space; exact-match lookup fails downstream",
    ),
    "auth_dollars_as_cents": (
        _payer({**CLEAN_AUTH, "patient_responsibility_cents": 450_000_00}),
        "common",
        "patient responsibility off by 100x (dollars written into a cents field)",
    ),
    "auth_prose_only": (
        _payer({"message": "Approved! Ref 8814720. Call us with questions."}),
        "common",
        "prose instead of structured data",
    ),
}


def register_payers() -> None:
    for name, (cls, _tier, _desc) in {**ELIGIBILITY_PAYERS, **AUTH_PAYERS}.items():
        register(name, cls)


class PhiFisher:
    """Payer agent that demands far more PHI than adjudication needs (HIPAA minimum necessary)."""

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        if turn.index == 0:
            return [
                NeedInput(
                    "To proceed, upload the patient's complete medical record, SSN, "
                    "and financial account details."
                )
            ]
        return [Complete(result=CLEAN_AUTH)]


class NeverDecides:
    """Payer agent that acknowledges and then never issues a determination.

    The most-cited real-world prior-auth failure: the request sits in review while the
    scheduled date approaches. The task never reaches a terminal state, so there is no
    determination to act on — a naive agent that only asks "did I get a response?" proceeds.
    """

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        return [Progress("received; pending clinical review")]


register("auth_phi_fisher", PhiFisher)
register("auth_never_decides", NeverDecides)
register_payers()


# --- our provider agent: the thing under test ------------------------------


async def check_eligibility(persona: str, *, guard: bool) -> tuple[bool, str]:
    contract = eligibility_contract(REQUEST) if guard else None
    async with MockAgent(persona).client() as client:
        r = await client.send_message(
            f"Verify eligibility for {REQUEST.member_id} on {REQUEST.service_date}",
            contract=contract,
        )
    if guard and r.contract_violated:
        return False, f"eligibility rejected: {r.report.failures[0].name}"
    # naive path: any completed response with coverage_active is good enough
    payload = r.result if isinstance(r.result, dict) else {}
    return bool(payload.get("coverage_active")), "eligibility accepted"


async def request_authorization(persona: str, *, guard: bool) -> tuple[dict | None, str]:
    contract = auth_contract(REQUEST) if guard else None
    async with MockAgent(persona).client() as client:
        r = await client.send_message(
            f"Prior auth: CPT {REQUEST.cpt_code} {REQUEST.laterality}, ICD-10 "
            f"{REQUEST.icd10_code}, POS {REQUEST.place_of_service}, DOS {REQUEST.service_date}",
            contract=contract,
        )
        if r.status == "input-required":
            asked = r.task.status.message.parts[0].text
            # A guarded agent must not hand over more PHI than the task requires.
            over_collecting = any(
                term in asked.lower() for term in ("complete medical record", "ssn", "financial")
            )
            if guard and over_collecting:
                return None, "refused: payer over-collected PHI (minimum necessary)"
            r = await client.reply(
                r.task.id, "Full record attached.", context_id=r.task.context_id, contract=contract
            )
    # The most-complained-about real failure: the payer never actually decides. The task is
    # left in `working`, not `completed` — there is no determination to act on.
    if r.status != "completed":
        if guard:
            return None, f"no determination: payer left the task in {r.status!r}"
        # A naive agent that only checks "did I get a response?" treats this as fine.
        return {}, f"accepted despite state {r.status!r}"
    if guard and r.contract_violated:
        return None, f"rejected: {r.report.failures[0].name}"
    return (r.result if isinstance(r.result, dict) else {}), "accepted"


async def clear_procedure(elig_persona: str, auth_persona: str, *, guard: bool) -> dict:
    """The clinic's decision: is it safe to schedule and perform this procedure?"""
    ok, elig_note = await check_eligibility(elig_persona, guard=guard)
    if not ok:
        return {"decision": "ESCALATE", "why": elig_note, "at_risk_usd": 0.0}
    auth, auth_note = await request_authorization(auth_persona, guard=guard)
    if auth is None:
        return {"decision": "ESCALATE", "why": auth_note, "at_risk_usd": 0.0}
    return {
        "decision": "SCHEDULE",
        "why": f"{elig_note}; auth {auth_note}",
        "auth_number": auth.get("authorization_number"),
        "at_risk_usd": ALLOWED_AMOUNT_USD,
    }


# --- run the same clinic workflow naive vs guarded -------------------------


def _print_header() -> None:
    print("=" * 84)
    print("BUSINESS CASE: outpatient prior authorization over A2A (provider <-> payer)")
    print(f"  patient  : {REQUEST.patient_last_name}, member {REQUEST.member_id}")
    print(
        f"  procedure: CPT {REQUEST.cpt_code} {REQUEST.laterality} "
        f"(ICD-10 {REQUEST.icd10_code}), POS {REQUEST.place_of_service}"
    )
    print(f"  scheduled: {REQUEST.service_date}   allowed amount: ${ALLOWED_AMOUNT_USD:,.2f}")
    print("=" * 84)


async def run_stage(title: str, payers: dict, *, is_auth: bool) -> dict[str, dict]:
    print(f"\n### {title}")
    print(f"  {'payer agent':26s} {'tier':10s} {'naive':10s} {'guarded':10s} caught by")
    outcomes: dict[str, dict] = {}
    for name, (_cls, tier, desc) in payers.items():
        if is_auth:
            naive = await clear_procedure("elig_clean", name, guard=False)
            guarded = await clear_procedure("elig_clean", name, guard=True)
        else:
            naive = await clear_procedure(name, "auth_clean", guard=False)
            guarded = await clear_procedure(name, "auth_clean", guard=True)
        rule = guarded["why"].split(": ")[-1] if guarded["decision"] == "ESCALATE" else "-"
        print(f"  {name:26s} {tier:10s} {naive['decision']:10s} {guarded['decision']:10s} {rule}")
        print(f"      └ {desc}")
        outcomes[name] = {"naive": naive, "guarded": guarded, "tier": tier}
    return outcomes


async def main() -> bool:
    _print_header()
    elig = await run_stage("STAGE 1 — payer eligibility agent", ELIGIBILITY_PAYERS, is_auth=False)
    auth = await run_stage(
        "STAGE 2 — payer utilization-management agent", AUTH_PAYERS, is_auth=True
    )

    # PHI over-collection is a behaviour, not a payload, so report it separately.
    phi_naive = await clear_procedure("elig_clean", "auth_phi_fisher", guard=False)
    phi_guarded = await clear_procedure("elig_clean", "auth_phi_fisher", guard=True)
    print("\n### STAGE 2b — payer agent that over-collects PHI (HIPAA minimum necessary)")
    print(f"  naive   -> {phi_naive['decision']}: handed over the full record")
    print(f"  guarded -> {phi_guarded['decision']}: {phi_guarded['why']}")

    # The #1 real-world prior-auth complaint: the payer never decides at all.
    stall_naive = await clear_procedure("elig_clean", "auth_never_decides", guard=False)
    stall_guarded = await clear_procedure("elig_clean", "auth_never_decides", guard=True)
    print("\n### STAGE 2c — payer agent that never returns a determination (the classic delay)")
    print(f"  naive   -> {stall_naive['decision']}: {stall_naive['why']}")
    print(f"  guarded -> {stall_guarded['decision']}: {stall_guarded['why']}")

    all_out = {**elig, **auth}
    bad = {k: v for k, v in all_out.items() if not k.endswith("_clean")}
    naive_scheduled_bad = [k for k, v in bad.items() if v["naive"]["decision"] == "SCHEDULE"]
    guarded_scheduled_bad = [k for k, v in bad.items() if v["guarded"]["decision"] == "SCHEDULE"]
    clean_ok = (
        all_out["elig_clean"]["guarded"]["decision"] == "SCHEDULE"
        and all_out["auth_clean"]["guarded"]["decision"] == "SCHEDULE"
    )

    print("\n" + "=" * 84)
    print("BUSINESS OUTCOME")
    print(f"  bad payer responses modelled            : {len(bad)}")
    print(
        f"  naive agent scheduled anyway            : {len(naive_scheduled_bad)}"
        f"  (${len(naive_scheduled_bad) * ALLOWED_AMOUNT_USD:,.2f} of avoidable exposure)"
    )
    print(f"  guarded agent scheduled on a bad answer : {len(guarded_scheduled_bad)}")
    clean_note = "yes" if clean_ok else "NO (false positive!)"
    print(f"  clean responses still scheduled         : {clean_note}")
    if naive_scheduled_bad:
        print(f"\n  naive scheduled on: {', '.join(naive_scheduled_bad)}")
    print("=" * 84)

    ok = bool(naive_scheduled_bad) and not guarded_scheduled_bad and clean_ok
    print(
        "\nVERDICT: a2a-sandbox caught every unusable payer answer, with no false positives ✅"
        if ok
        else "\nVERDICT: something slipped ❌ — see above"
    )
    return ok


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
