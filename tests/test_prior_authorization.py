"""Healthcare revenue-cycle scenario: prior authorization over A2A.

Mirrors examples/prior_authorization.py (which carries the source citations). A different
failure shape from the freight example: one authoritative payer answers, and the danger is
that its answer does not match the request. Every payer here reports the task "completed".

Two properties matter equally, and the second is the one that keeps the tool usable:
  1. every unusable payer answer is caught, by the rule that should catch it
  2. legitimate payer variation is NOT flagged — including X12 review-action codes. An
     over-strict contract gets switched off, which is worse than no contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import BaseModel

from counterpart import Contract, MockAgent
from counterpart.core.behaviour import (
    Complete,
    Directive,
    NeedInput,
    Progress,
    SessionContext,
    Turn,
)
from counterpart.personas import PersonaFactory, register

TODAY = datetime.now(UTC).date()
SERVICE_DATE = (TODAY + timedelta(days=4)).isoformat()
LAST_MONTH = (TODAY - timedelta(days=30)).isoformat()
NEXT_MONTH = (TODAY + timedelta(days=35)).isoformat()
IN_A_YEAR = (TODAY + timedelta(days=365)).isoformat()

NON_COVERED_DX = {"M17.0", "M17.10", "M17.11", "M17.12", "M17.9"}  # CMS NCD 150.9
_APPROVES = {"A1", "APPROVED", "CERTIFIED IN TOTAL", "CERT", "CERTIFIED"}
_CAVEAT_WORDS = ("pending", "contingent", "upon receipt", "subject to", "must submit")


@dataclass(frozen=True)
class AuthRequest:
    member_id: str = "W123456789"
    cpt_code: str = "29881"
    icd10_code: str = "M23.221"
    laterality: str = "RT"
    place_of_service: str = "22"
    service_type_code: str = "50"
    rendering_provider_npi: str = "1730164412"
    service_date: str = SERVICE_DATE


REQUEST = AuthRequest()


class Eligibility(BaseModel):
    member_id: str
    coverage_active: bool
    coverage_start: str
    coverage_end: str | None = None
    covered_service_types: list[str] = []
    other_payer_is_primary: bool = False
    reject_reason_code: str | None = None
    deductible_remaining_cents: int | None = None
    deductible_as_of: str | None = None


class AuthDetermination(BaseModel):
    authorization_number: str | None = None
    determination: str | None = None
    review_action_code: str | None = None
    authorization_required: bool | None = None
    member_id: str
    cpt_codes: list[str] = []
    diagnosis_codes: list[str] = []
    laterality: str | None = None
    place_of_service: str | None = None
    rendering_provider_npi: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    provisional: bool = False
    conditions: list[str] = []
    notes: str = ""
    patient_responsibility_cents: int | None = None


def _iso_date(value: str | None) -> date | None:
    """Strict ISO parse. A naive string compare against "07/31/2026" silently passes a
    lower-bound check (fails open), so anything non-ISO is treated as unusable."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _same_id(returned: str | None, requested: str) -> bool:
    """Correct in BOTH directions: a raw == rejects the correct member when the payer echoes
    with different case/padding; a substring check would accept a different member."""
    if returned is None:
        return False
    return returned.strip().casefold() == requested.strip().casefold()


def _is_approval(det: AuthDetermination) -> bool:
    """Approval per the X12 review action code when present, else the free text.
    Unknown values fail closed."""
    code = (det.review_action_code or "").strip().upper()
    if code:
        return code in _APPROVES
    return (det.determination or "").strip().upper() in _APPROVES


def eligibility_contract(req: AuthRequest) -> Contract:
    service_date = date.fromisoformat(req.service_date)
    return (
        Contract("payer eligibility check")
        .returns(Eligibility)
        .require("is_a_coverage_answer", lambda e: e.reject_reason_code is None)
        .require("member_matches", lambda e: _same_id(e.member_id, req.member_id))
        .require("coverage_active", lambda e: e.coverage_active is True)
        .require(
            "covers_this_service_type",
            lambda e: req.service_type_code in e.covered_service_types,
        )
        .require("this_payer_is_primary", lambda e: e.other_payer_is_primary is False)
        .require(
            "active_on_service_date",
            lambda e: (end := _iso_date(e.coverage_end)) is None or end >= service_date,
        )
        .require(
            "coverage_started",
            lambda e: (s := _iso_date(e.coverage_start)) is not None and s <= service_date,
        )
        .require(
            "accumulator_is_current",
            lambda e: (
                e.deductible_remaining_cents is None
                or ((asof := _iso_date(e.deductible_as_of)) is not None and asof >= TODAY)
            ),
        )
        .expect_status("completed")
    )


def auth_contract(req: AuthRequest) -> Contract:
    service_date = date.fromisoformat(req.service_date)
    return (
        Contract("prior authorization")
        .returns(AuthDetermination)
        .require(
            "no_unsubstantiated_waiver",
            lambda a: a.authorization_required is not False or bool(a.authorization_number),
        )
        .require("certified", _is_approval)
        .require(
            "has_authorization_number",
            lambda a: (
                bool(a.authorization_number)
                and a.authorization_number == a.authorization_number.strip()
            ),
        )
        .require("not_provisional", lambda a: a.provisional is False and not a.conditions)
        .require(
            "no_caveat_in_notes",
            lambda a: not any(w in a.notes.lower() for w in _CAVEAT_WORDS),
        )
        .require("member_matches", lambda a: _same_id(a.member_id, req.member_id))
        .require("covers_requested_cpt", lambda a: req.cpt_code in a.cpt_codes)
        .require("diagnosis_is_covered", lambda a: not (set(a.diagnosis_codes) & NON_COVERED_DX))
        .require("laterality_matches", lambda a: (a.laterality or "").upper() == req.laterality)
        .require("place_of_service_matches", lambda a: a.place_of_service == req.place_of_service)
        .require(
            "rendering_npi_matches",
            lambda a: (
                a.rendering_provider_npi is None
                or _same_id(a.rendering_provider_npi, req.rendering_provider_npi)
            ),
        )
        .require(
            "window_covers_service_date",
            lambda a: (
                (eff := _iso_date(a.effective_date)) is not None
                and (exp := _iso_date(a.expiration_date)) is not None
                and eff <= service_date <= exp
            ),
        )
        .require(
            "patient_responsibility_plausible",
            lambda a: (
                a.patient_responsibility_cents is None
                or 0 <= a.patient_responsibility_cents <= 2_000_000
            ),
        )
        .expect_status("completed")
    )


CLEAN_ELIGIBILITY = {
    "member_id": REQUEST.member_id,
    "coverage_active": True,
    "coverage_start": "2026-01-01",
    "coverage_end": None,
    "covered_service_types": ["30", "50"],
    "other_payer_is_primary": False,
    "deductible_remaining_cents": 45_000,
    "deductible_as_of": TODAY.isoformat(),
}

CLEAN_AUTH = {
    "authorization_number": "AUTH20268814720",
    "determination": "APPROVED",
    "review_action_code": "A1",
    "member_id": REQUEST.member_id,
    "cpt_codes": [REQUEST.cpt_code],
    "diagnosis_codes": [REQUEST.icd10_code],
    "laterality": REQUEST.laterality,
    "place_of_service": REQUEST.place_of_service,
    "rendering_provider_npi": REQUEST.rendering_provider_npi,
    "effective_date": TODAY.isoformat(),
    "expiration_date": IN_A_YEAR,
    "patient_responsibility_cents": 45_000,
}


class _FixedPayer:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        return [Progress("adjudicating"), Complete(result=self._payload)]


def _payer(payload: object) -> PersonaFactory:
    return lambda **_: _FixedPayer(payload)


# (payload, rule that must catch it). All report "completed".
BAD_AUTH: dict[str, tuple[dict, str]] = {
    # HL7 Da Vinci PAS's own pended example: outcome=complete (REQUIRED field) with the truth
    # only in the OPTIONAL reviewActionCode A4.
    "pended_as_complete": (
        {
            **CLEAN_AUTH,
            "determination": "complete",
            "review_action_code": "A4",
            "authorization_number": None,
        },
        "certified",
    ),
    "reference_not_authorization": (
        {
            **CLEAN_AUTH,
            "determination": None,
            "review_action_code": "A4",
            "authorization_number": "0000123456789012",
        },
        "certified",
    ),
    "not_required_waiver": (
        {
            "member_id": REQUEST.member_id,
            "authorization_required": False,
            "authorization_number": None,
        },
        "no_unsubstantiated_waiver",
    ),
    "denied_a3": (
        {**CLEAN_AUTH, "determination": "DENIED", "review_action_code": "A3"},
        "certified",
    ),
    "unknown_determination": (
        {**CLEAN_AUTH, "determination": "REVIEWED", "review_action_code": None},
        "certified",
    ),
    "cpt_drift": ({**CLEAN_AUTH, "cpt_codes": ["29877"]}, "covers_requested_cpt"),
    "provisional": (
        {**CLEAN_AUTH, "provisional": True, "conditions": ["op note due in 30 days"]},
        "not_provisional",
    ),
    "caveat_in_notes": (
        {**CLEAN_AUTH, "notes": "Approved pending receipt of the operative note."},
        "no_caveat_in_notes",
    ),
    "pos_redirect_asc": ({**CLEAN_AUTH, "place_of_service": "24"}, "place_of_service_matches"),
    "date_format_slip": (
        {**CLEAN_AUTH, "effective_date": "07/31/2026", "expiration_date": "07/31/2027"},
        "window_covers_service_date",
    ),
    "window_starts_late": (
        {**CLEAN_AUTH, "effective_date": NEXT_MONTH},
        "window_covers_service_date",
    ),
    "non_covered_dx": ({**CLEAN_AUTH, "diagnosis_codes": ["M17.11"]}, "diagnosis_is_covered"),
    "dollars_as_cents": (
        {**CLEAN_AUTH, "patient_responsibility_cents": 450_000_00},
        "patient_responsibility_plausible",
    ),
    "approved_without_number": (
        {**CLEAN_AUTH, "authorization_number": None},
        "has_authorization_number",
    ),
    "setting_not_stated": ({**CLEAN_AUTH, "place_of_service": None}, "place_of_service_matches"),
    "wrong_rendering_npi": (
        {**CLEAN_AUTH, "rendering_provider_npi": "1043210987"},
        "rendering_npi_matches",
    ),
    "modifier_stripped": ({**CLEAN_AUTH, "laterality": None}, "laterality_matches"),
    "wrong_laterality": ({**CLEAN_AUTH, "laterality": "LT"}, "laterality_matches"),
    "wrong_member": ({**CLEAN_AUTH, "member_id": "W555000111"}, "member_matches"),
    "whitespace_auth_number": (
        {**CLEAN_AUTH, "authorization_number": "AUTH20268814720 "},
        "has_authorization_number",
    ),
}

BAD_ELIGIBILITY: dict[str, tuple[dict, str]] = {
    "aaa_reject_as_answer": (
        {**CLEAN_ELIGIBILITY, "coverage_active": False, "reject_reason_code": "72"},
        "is_a_coverage_answer",
    ),
    "generic_benefit_only": (
        {**CLEAN_ELIGIBILITY, "covered_service_types": ["30"]},
        "covers_this_service_type",
    ),
    "other_payer_primary": (
        {**CLEAN_ELIGIBILITY, "other_payer_is_primary": True},
        "this_payer_is_primary",
    ),
    "retro_termed": ({**CLEAN_ELIGIBILITY, "coverage_end": LAST_MONTH}, "active_on_service_date"),
    "future_coverage": ({**CLEAN_ELIGIBILITY, "coverage_start": NEXT_MONTH}, "coverage_started"),
    "wrong_member": ({**CLEAN_ELIGIBILITY, "member_id": "W999888777"}, "member_matches"),
    "stale_accumulator": (
        {**CLEAN_ELIGIBILITY, "deductible_as_of": LAST_MONTH},
        "accumulator_is_current",
    ),
}

# Legitimate variation that MUST be accepted. The X12 code cases were found by researching the
# real value sets — an earlier version of this contract false-positived on both.
GOOD_AUTH: dict[str, dict] = {
    "x12_a1_code": {**CLEAN_AUTH, "determination": None, "review_action_code": "A1"},
    "certified_in_total_text": {
        **CLEAN_AUTH,
        "determination": "Certified in total",
        "review_action_code": None,
    },
    "lowercase_determination": {**CLEAN_AUTH, "determination": "approved"},
    "cpt_superset": {**CLEAN_AUTH, "cpt_codes": ["29881", "29877"]},
    "effective_on_service_date": {**CLEAN_AUTH, "effective_date": SERVICE_DATE},
    "expires_on_service_date": {**CLEAN_AUTH, "expiration_date": SERVICE_DATE},
    "lowercase_laterality": {**CLEAN_AUTH, "laterality": "rt"},
    "zero_patient_responsibility": {**CLEAN_AUTH, "patient_responsibility_cents": 0},
    "omits_cost_share": {
        k: v for k, v in CLEAN_AUTH.items() if k != "patient_responsibility_cents"
    },
    "informational_notes": {**CLEAN_AUTH, "notes": "Certified. Call with questions."},
    "waiver_with_number": {**CLEAN_AUTH, "authorization_required": False},
    "member_id_echo_padded": {**CLEAN_AUTH, "member_id": f"  {REQUEST.member_id.lower()} "},
    "npi_omitted": {k: v for k, v in CLEAN_AUTH.items() if k != "rendering_provider_npi"},
}
GOOD_ELIGIBILITY: dict[str, dict] = {
    "coverage_ends_on_service_date": {**CLEAN_ELIGIBILITY, "coverage_end": SERVICE_DATE},
    "coverage_starts_today": {**CLEAN_ELIGIBILITY, "coverage_start": TODAY.isoformat()},
    "extra_service_types": {**CLEAN_ELIGIBILITY, "covered_service_types": ["30", "50", "98"]},
    "accumulator_as_of_today": {**CLEAN_ELIGIBILITY, "deductible_as_of": TODAY.isoformat()},
    "no_accumulator_at_all": {
        k: v
        for k, v in CLEAN_ELIGIBILITY.items()
        if k not in ("deductible_remaining_cents", "deductible_as_of")
    },
}

for _n, (_p, _r) in BAD_AUTH.items():
    register(f"pa_auth_bad_{_n}", _payer(_p))
for _n, (_p, _r) in BAD_ELIGIBILITY.items():
    register(f"pa_elig_bad_{_n}", _payer(_p))
for _n, _p in GOOD_AUTH.items():
    register(f"pa_auth_ok_{_n}", _payer(_p))
for _n, _p in GOOD_ELIGIBILITY.items():
    register(f"pa_elig_ok_{_n}", _payer(_p))
register("pa_auth_clean", _payer(CLEAN_AUTH))
register("pa_elig_clean", _payer(CLEAN_ELIGIBILITY))


async def _ask(persona: str, contract: Contract):
    async with MockAgent(persona).client() as client:
        return await client.send_message("278 review request", contract=contract)


# --- the two properties that matter ----------------------------------------


async def test_control_responses_are_accepted() -> None:
    assert not (await _ask("pa_auth_clean", auth_contract(REQUEST))).contract_violated
    assert not (await _ask("pa_elig_clean", eligibility_contract(REQUEST))).contract_violated


@pytest.mark.parametrize("name", list(BAD_AUTH))
async def test_bad_authorization_is_caught_by_the_right_rule(name: str) -> None:
    expected_rule = BAD_AUTH[name][1]
    result = await _ask(f"pa_auth_bad_{name}", auth_contract(REQUEST))
    assert result.status == "completed", f"{name}: payer claimed protocol success"
    assert result.contract_violated, f"{name}: slipped through"
    failed = [c.name for c in result.report.failures]
    assert expected_rule in failed, f"{name}: expected {expected_rule}, got {failed}"


@pytest.mark.parametrize("name", list(BAD_ELIGIBILITY))
async def test_bad_eligibility_is_caught_by_the_right_rule(name: str) -> None:
    expected_rule = BAD_ELIGIBILITY[name][1]
    result = await _ask(f"pa_elig_bad_{name}", eligibility_contract(REQUEST))
    assert result.status == "completed"
    assert result.contract_violated, f"{name}: slipped through"
    failed = [c.name for c in result.report.failures]
    assert expected_rule in failed, f"{name}: expected {expected_rule}, got {failed}"


@pytest.mark.parametrize("name", list(GOOD_AUTH))
async def test_legitimate_authorization_variation_is_accepted(name: str) -> None:
    """No false positives. An over-strict contract gets disabled — worse than none."""
    result = await _ask(f"pa_auth_ok_{name}", auth_contract(REQUEST))
    assert not result.contract_violated, (
        f"FALSE POSITIVE on {name}: {[c.name for c in result.report.failures]}"
    )


@pytest.mark.parametrize("name", list(GOOD_ELIGIBILITY))
async def test_legitimate_eligibility_variation_is_accepted(name: str) -> None:
    result = await _ask(f"pa_elig_ok_{name}", eligibility_contract(REQUEST))
    assert not result.contract_violated, (
        f"FALSE POSITIVE on {name}: {[c.name for c in result.report.failures]}"
    )


# --- the conceptual point + regressions for two real bugs ------------------


async def test_pended_response_claiming_complete_is_the_headline_case() -> None:
    """HL7 Da Vinci PAS ships this exact shape: `outcome: "complete"` (a REQUIRED field) on a
    response whose only truthful signal is the OPTIONAL reviewActionCode A4 = pended.

    A consumer reading the top-level status cannot distinguish it from an approval.
    """
    result = await _ask("pa_auth_bad_pended_as_complete", auth_contract(REQUEST))
    assert result.completed  # the A2A task genuinely completed
    assert result.status == "completed"
    assert result.contract_violated  # but nothing was certified
    assert "certified" in [c.name for c in result.report.failures]


def test_unknown_determination_values_fail_closed() -> None:
    """Regression: an unrecognised approval string must never be read as an approval."""
    for value in ("REVIEWED", "IN PROCESS", "A4", "A2", "A6", "", "MODIFIED"):
        det = AuthDetermination(member_id=REQUEST.member_id, determination=value)
        assert not _is_approval(det), f"{value!r} must not count as certified"


def test_identity_comparison_is_correct_in_both_directions() -> None:
    """Regression for a bug forums surfaced: payers echo member ids with different case and
    padding, so a raw `==` rejects the CORRECT member; a substring check would accept a
    DIFFERENT one."""
    assert _same_id("  w123456789 ", "W123456789")  # correct member, ugly echo -> accept
    assert _same_id("W123456789", "W123456789")
    assert not _same_id("W123456789X", "W123456789")  # prefix/superstring -> reject
    assert not _same_id("W999888777", "W123456789")
    assert not _same_id(None, "W123456789")


def test_x12_approval_codes_are_recognised() -> None:
    """Regression for a real false positive: A1 and its display string ARE approvals.

    An earlier version of this contract only accepted the literal string "APPROVED" and would
    have escalated every X12-native certification.
    """
    for value in ("A1", "a1", "APPROVED", "approved", "Certified in total", "CERT"):
        det = AuthDetermination(member_id=REQUEST.member_id, determination=value)
        assert _is_approval(det), f"{value!r} is a legitimate certification"


def test_non_iso_dates_are_rejected_not_silently_compared() -> None:
    """Regression: string-comparing "07/31/2026" against ISO silently passes a lower bound."""
    assert _iso_date("2026-07-31") == date(2026, 7, 31)
    assert _iso_date("07/31/2026") is None  # unusable, not "less than everything"
    assert _iso_date(None) is None
    # The dangerous direction, demonstrated: raw string compare would have passed.
    assert "07/31/2026" <= "2026-07-31"


# --- behavioural failures --------------------------------------------------


async def test_payer_that_never_decides_yields_no_determination() -> None:
    class NeverDecides:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            return [Progress("received; pending clinical review")]

    register("pa_auth_never_decides", NeverDecides)
    result = await _ask("pa_auth_never_decides", auth_contract(REQUEST))
    assert not result.completed
    assert result.status == "working"
    assert result.contract_violated


async def test_phi_over_collection_is_visible_to_the_caller() -> None:
    """HIPAA minimum necessary: the policy needs failed-conservative-therapy documentation,
    not 24 months of complete records."""

    class PhiOverCollector:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            if turn.index == 0:
                return [NeedInput("Upload the complete medical record for the past 24 months.")]
            return [Complete(result=CLEAN_AUTH)]

    register("pa_auth_phi", PhiOverCollector)
    async with MockAgent("pa_auth_phi").client() as client:
        first = await client.send_message("278 review request")
    assert first.reached_state("input-required")
    asked = first.task.status.message.parts[0].text.lower()
    assert "complete medical record" in asked  # the caller can inspect and refuse


async def test_non_idempotent_retry_yields_two_authorization_numbers() -> None:
    """A retry opens a second case. Note: counterpart gives each task its own behaviour
    instance, so modelling cross-task memory requires explicit class-level state."""

    class DuplicateOnRetry:
        _n = 0

        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            type(self)._n += 1
            return [
                Complete(
                    result={**CLEAN_AUTH, "authorization_number": f"AUTH2026881{type(self)._n:04d}"}
                )
            ]

    register("pa_auth_dup", DuplicateOnRetry)
    first = await _ask("pa_auth_dup", auth_contract(REQUEST))
    second = await _ask("pa_auth_dup", auth_contract(REQUEST))
    # Each response is individually valid — the defect only exists across calls.
    assert not first.contract_violated and not second.contract_violated
    assert first.result["authorization_number"] != second.result["authorization_number"]
