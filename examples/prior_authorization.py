"""Dogfood #2 — healthcare revenue cycle: prior authorization over A2A.

A different vertical and a different FAILURE SHAPE from examples/freight_procurement.py.
Freight was "pick the cheapest valid quote from N competing offers." This is a *pipeline with
cross-field consistency*: one authoritative payer answers, and the danger is that its answer
does not match what was asked.

    ProviderRevenueAgent (a clinic)
      1. --A2A--> payer ELIGIBILITY agent      : coverage active for THIS service, on THIS date?
      2. --A2A--> payer UTILIZATION MGMT agent : certify CPT 29881 RT for 2026-xx-xx
      3. decide: safe to schedule, or escalate to a human?

WHY THIS IS THE HARD CASE: in A2A, `completed` means *the agent finished its work*, not *you
got what you asked for*. Conformance testing cannot see the difference. The failure modes below
are drawn from the actual standards and industry data, not invented:

* HL7 Da Vinci PAS is the FHIR standard for prior auth. Its own published PENDED example ships
  `outcome: "complete"` with no `preAuthRef`, and its APPROVED example is ALSO
  `outcome: "complete"` with no `preAuthRef` — the two differ only in an OPTIONAL (0..1)
  `reviewAction.reviewActionCode` (A1 certified vs A4 pended), while the misleading `outcome`
  field is REQUIRED (1..1). A consumer reading the top-level status literally cannot tell an
  approval from a pend. Verified present in STU2.1 and still in the current CI build.
  https://hl7.org/fhir/us/davinci-pas/STU2.1/specification.html
* In X12 278, a first response is routinely an interim acknowledgement, not a decision:
  Blue Cross NC returns `BHT06=19` / `HCR01=A4` (pended) within 24 hours and the determination
  only in a later, *unsolicited* transaction. Texas Medicaid returns `A4` "for all approved
  transactions" — there, A4 is a receipt, not an approval.
  https://www.bcbsnc.com/content/dam/bcbsnc/pdf/providers/network-participation/hipaa/278-5010-v1-1-health-care-service-and-review.pdf
* Authorization is not immunity from denial. Premier reports that "An average of 10.4 percent
  of claims denied included those that were pre-approved via the prior authorization process -
  up from 3.2 percent in 2022," at a rework cost of $57.23 per claim (up from $43.84 in 2022).
  https://premierinc.com/newsroom/policy/claims-adjudication-costs-providers-257-billion-18-billion-is-potentially-unnecessary-expense

Practitioner reports (AAPC forums and r/CodingandBilling, reached via public archives) supply
the messier long tail, including the two best-attested shapes below:
* An approval that never produced a number — "they got the auth but it didn't generate an
  approval number" — so there is nothing to put in box 23 of the claim. This is the *real*
  version of a "placeholder" auth number: the field is null or absent while status says
  approved. https://www.reddit.com/r/CodingandBilling/comments/1qkk5oi/
* Payers declining to put a no-auth-needed answer in writing — "they refuse to provide anything
  in writing that the procedure doesn't need prior authorization" — which is why an
  unsubstantiated `authorization_required: false` is worthless.
  https://www.aapc.com/discuss/threads/claim-denied-for-no-authorization.157273/
* Denials whose stated reason is internal jargon — e.g. "Included with Primary Code Review" —
  that the provider cannot act on.
  https://www.aapc.com/discuss/threads/umr-voiding-prior-authorizations.204030/

HONEST SOURCING. Each case below is labelled with how well it is attested. Two are marked
`unattested`: a targeted search of those communities for wrong-side/laterality authorization
defects found none (the laterality threads that exist are provider-side coding questions), and
integer-cents-vs-dollars is a generic REST integration bug rather than a reported payer
behaviour. They are kept because the *consequence* is severe and cheap to guard, not because
the frequency is established. Reddit, chat.fhir.org, HL7 JIRA and the Availity/Optum/HFMA
communities were wholly or partly unreachable, so absence of evidence here is weak evidence.

Deliberately NOT modelled (strawmen a schema catches for free): literal sentinel auth numbers
like "N/A"/"TBD" typed into a portal; a knee arthroscopy "downgraded" to a physician office
(POS 11), which no payer would return; negative deductibles; and a bare prose reply. Each is
replaced below by the realistic version of the same idea.

Run:  uv run python examples/prior_authorization.py
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

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
from counterpart.personas import register

TODAY = datetime.now(UTC).date()
SERVICE_DATE = (TODAY + timedelta(days=4)).isoformat()
LAST_MONTH = (TODAY - timedelta(days=30)).isoformat()
NEXT_MONTH = (TODAY + timedelta(days=35)).isoformat()
IN_A_YEAR = (TODAY + timedelta(days=365)).isoformat()

# Commercial median allowed amount, hospital outpatient knee arthroscopy (Robinson et al.
# via the research notes; Medicare HOPD is ~$3,342, so this is the commercial middle).
ALLOWED_AMOUNT_USD = 5_668.00
DENIAL_REWORK_USD = 57.23  # Premier, 2023 per-claim rework cost


# --- what we asked for -----------------------------------------------------


@dataclass(frozen=True)
class AuthRequest:
    member_id: str = "W123456789"
    cpt_code: str = "29881"  # knee arthroscopy w/ meniscectomy (1 unit; RT modifier)
    icd10_code: str = "M23.221"  # medial meniscus derangement, right knee
    laterality: str = "RT"
    place_of_service: str = "22"  # on-campus hospital outpatient
    service_type_code: str = "50"  # X12 271 service type: Hospital-Outpatient
    rendering_provider_npi: str = "1730164412"  # the surgeon who will actually operate
    service_date: str = SERVICE_DATE


REQUEST = AuthRequest()

# ICD-10 codes CMS NCD 150.9 makes non-covered for arthroscopic lavage/debridement of the
# osteoarthritic knee. An auth "approved" against one of these will not survive adjudication.
NON_COVERED_DX = {"M17.0", "M17.10", "M17.11", "M17.12", "M17.9"}


# --- payer response shapes -------------------------------------------------


class Eligibility(BaseModel):
    member_id: str
    plan_name: str
    coverage_active: bool
    coverage_start: str
    coverage_end: str | None = None
    # X12 271: which service types this benefit actually covers. `EB03=30` alone means only
    # "Health Benefit Plan Coverage" — a generic yes that says nothing about our service.
    covered_service_types: list[str] = []
    other_payer_is_primary: bool = False
    reject_reason_code: str | None = None  # 271 AAA reject: 42/72/75/80 — not a coverage answer
    deductible_remaining_cents: int | None = None
    # Accumulators are frequently stale: the figure is real but was true days ago. Quoting a
    # patient from a stale accumulator is the attested money defect (not cents-vs-dollars).
    deductible_as_of: str | None = None


class AuthDetermination(BaseModel):
    authorization_number: str | None = None
    determination: str | None = None  # APPROVED | A1 | Certified in total | A4 | DENIED | ...
    review_action_code: str | None = None  # X12 306: A1 certified, A4 pended, A3 not certified
    authorization_required: bool | None = None
    member_id: str
    cpt_codes: list[str] = []
    diagnosis_codes: list[str] = []
    laterality: str | None = None
    place_of_service: str | None = None
    # An auth is bound to a rendering provider; one issued against the requesting or a
    # different NPI is real, and unusable by the surgeon who will operate.
    rendering_provider_npi: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    provisional: bool = False
    conditions: list[str] = []
    notes: str = ""
    patient_responsibility_cents: int | None = None


# --- the clinic's policy, as machine-checkable contract rules --------------

# X12 306 review action codes and their plain-language equivalents. Anything not in APPROVES
# is treated as *not* an approval — including values we have never seen (fail closed).
_APPROVES = {"A1", "APPROVED", "CERTIFIED IN TOTAL", "CERT", "CERTIFIED"}
_NOT_APPROVES = {
    "A2": "certified partial",
    "A3": "not certified",
    "A4": "pended",
    "A6": "modified",
    "PENDED": "pended",
    "PENDING": "pended",
    "DENIED": "denied",
    "NOT CERTIFIED": "not certified",
}
# Caveat language that turns an "approval" into a conditional one.
_CAVEAT_WORDS = (
    "pending",
    "contingent",
    "upon receipt",
    "subject to",
    "must submit",
    "provided that",
)


def _iso_date(value: str | None) -> date | None:
    """Strict ISO-8601 parse. Returns None for absent or non-ISO input.

    Deliberately strict: a naive string comparison against a "07/31/2026"-style date silently
    *passes* the lower bound of a window check, which fails open. Unparseable means unusable.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _same_id(returned: str | None, requested: str) -> bool:
    """Identity comparison that is correct in BOTH directions.

    Payers echo member ids with different case and padding (" zgd123456789 " for
    "ZGD123456789"), so a raw `==` rejects the *correct* member — a false positive. A substring
    or prefix check would go the other way and accept a *different* member. Normalise, then
    require full equality.
    """
    if returned is None:
        return False
    return returned.strip().casefold() == requested.strip().casefold()


def _is_approval(det: AuthDetermination) -> bool:
    """True only if the payer clearly certified the request.

    Reads the X12 review action code when present (it is the element that actually carries
    approval state) and the free-text determination otherwise. Unknown values fail closed.
    """
    code = (det.review_action_code or "").strip().upper()
    if code:
        return code in _APPROVES
    text = (det.determination or "").strip().upper()
    return text in _APPROVES


def eligibility_contract(req: AuthRequest) -> Contract:
    return (
        Contract("payer eligibility check")
        .returns(Eligibility)
        # A 271 AAA reject is a "we could not answer", NOT a "not covered". Treating it as a
        # coverage answer turns a covered patient away — the one failure whose victim is the
        # patient rather than the provider.
        .require("is_a_coverage_answer", lambda e: e.reject_reason_code is None)
        .require("member_matches", lambda e: _same_id(e.member_id, req.member_id))
        .require("coverage_active", lambda e: e.coverage_active is True)
        # "Active coverage" alone is the weakest assertion in a 271: it must cover the service
        # type we are about to render.
        .require(
            "covers_this_service_type", lambda e: req.service_type_code in e.covered_service_types
        )
        # If another payer is primary, this plan is not the one liable.
        .require("this_payer_is_primary", lambda e: e.other_payer_is_primary is False)
        .require(
            "active_on_service_date",
            lambda e: (
                (end := _iso_date(e.coverage_end)) is None
                or end >= date.fromisoformat(req.service_date)
            ),
        )
        .require(
            "coverage_started",
            lambda e: (
                (start := _iso_date(e.coverage_start)) is not None
                and start <= date.fromisoformat(req.service_date)
            ),
        )
        # A real accumulator figure that was true days ago still misquotes the patient today.
        # (This, not integer-cents-vs-dollars, is the money defect practitioners report.)
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
    """Every rule maps to a documented denial cause. See the module docstring for sources."""
    service_date = date.fromisoformat(req.service_date)
    return (
        Contract(f"prior auth: CPT {req.cpt_code} {req.laterality} on {req.service_date}")
        .returns(AuthDetermination)
        # 1. An "authorization not required" answer must be substantiated, not bare — payers
        #    routinely delegate MSK/radiology to a benefit manager and answer for themselves.
        .require(
            "no_unsubstantiated_waiver",
            lambda a: a.authorization_required is not False or bool(a.authorization_number),
        )
        # 2. Certified — reading the X12 review action code, failing closed on unknown values.
        .require("certified", _is_approval)
        # 3. An authorization number that represents certification, not just a tracking id.
        .require(
            "has_authorization_number",
            lambda a: (
                bool(a.authorization_number)
                and a.authorization_number == a.authorization_number.strip()
            ),
        )
        # 4. Not provisional/conditional — reimbursement contingent on later documentation.
        .require("not_provisional", lambda a: a.provisional is False and not a.conditions)
        # 5. No decisive caveat hiding in free text (the classic LLM-payer failure: a
        #    schema-valid payload whose real meaning lives in a notes field nobody reads).
        .require(
            "no_caveat_in_notes",
            lambda a: not any(w in a.notes.lower() for w in _CAVEAT_WORDS),
        )
        # 6. Our patient (record overlay / duplicate-MRN errors are also a HIPAA disclosure).
        .require("member_matches", lambda a: _same_id(a.member_id, req.member_id))
        # 7. The procedure we asked for (code drift to a related, cheaper, bundled code).
        .require("covers_requested_cpt", lambda a: req.cpt_code in a.cpt_codes)
        # 8. Not certified against a nationally non-covered diagnosis (CMS NCD 150.9).
        .require(
            "diagnosis_is_covered",
            lambda a: not (set(a.diagnosis_codes) & NON_COVERED_DX),
        )
        # 9. Correct side — wrong laterality is a wrong-site and denial risk.
        .require("laterality_matches", lambda a: (a.laterality or "").upper() == req.laterality)
        # 10. Same setting we requested (MSK site-of-service redirection to an ASC is common),
        #     and the setting must be STATED — an approval that omits it withholds the exact key
        #     the claim is adjudicated against.
        .require("place_of_service_matches", lambda a: a.place_of_service == req.place_of_service)
        # 11. Bound to the surgeon who will actually operate.
        .require(
            "rendering_npi_matches",
            lambda a: (
                a.rendering_provider_npi is None
                or _same_id(a.rendering_provider_npi, req.rendering_provider_npi)
            ),
        )
        # 11. A parseable window that actually covers the service date.
        .require(
            "window_covers_service_date",
            lambda a: (
                (eff := _iso_date(a.effective_date)) is not None
                and (exp := _iso_date(a.expiration_date)) is not None
                and eff <= service_date <= exp
            ),
        )
        # 12. Patient responsibility sane — a cents/dollars slip misquotes the patient under
        #     the No Surprises Act.
        .require(
            "patient_responsibility_plausible",
            lambda a: (
                a.patient_responsibility_cents is None
                or 0 <= a.patient_responsibility_cents <= 2_000_000
            ),
        )
        .expect_status("completed")
    )


# --- baseline good responses ----------------------------------------------

CLEAN_ELIGIBILITY = {
    "member_id": REQUEST.member_id,
    "plan_name": "Harbor PPO Gold",
    "coverage_active": True,
    "coverage_start": "2026-01-01",
    "coverage_end": None,
    "covered_service_types": ["30", "50", "98"],
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


def _payer(payload: object, *, progress: str = "adjudicating") -> type:
    class FixedPayer:
        def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
            return [Progress(progress), Complete(result=payload)]

    return FixedPayer


# --- the failure catalogue -------------------------------------------------
# (persona name) -> (payload, tier, what it models)

ELIGIBILITY_CASES: dict[str, tuple[dict, str, str]] = {
    "elig_clean": (CLEAN_ELIGIBILITY, "control", "active, correct member, covers our service type"),
    "elig_aaa_reject_as_answer": (
        {**CLEAN_ELIGIBILITY, "coverage_active": False, "reject_reason_code": "72"},
        "very common",
        "271 AAA reject (72 invalid subscriber id) read as 'no coverage' -> patient turned away",
    ),
    "elig_generic_benefit_only": (
        {**CLEAN_ELIGIBILITY, "covered_service_types": ["30"]},
        "very common",
        "EB01=1 active with only EB03=30 generic; no benefit for service type 50",
    ),
    "elig_other_payer_primary": (
        {**CLEAN_ELIGIBILITY, "other_payer_is_primary": True},
        "very common",
        "coordination-of-benefits: this plan is not the liable payer",
    ),
    "elig_retro_termed": (
        {**CLEAN_ELIGIBILITY, "coverage_end": LAST_MONTH},
        "common",
        "reports active, but the plan terminated before the service date",
    ),
    "elig_future_coverage": (
        {**CLEAN_ELIGIBILITY, "coverage_start": NEXT_MONTH},
        "common",
        "coverage does not begin until after the service date",
    ),
    "elig_stale_accumulator": (
        {**CLEAN_ELIGIBILITY, "deductible_as_of": LAST_MONTH},
        "very common",
        "real deductible figure, but as-of last month — misquotes the patient today",
    ),
    "elig_wrong_member": (
        {**CLEAN_ELIGIBILITY, "member_id": "W999888777"},
        "long-tail",
        "another member's coverage returned (record overlay) — also a HIPAA disclosure",
    ),
}

AUTH_CASES: dict[str, tuple[dict, str, str]] = {
    "auth_clean": (CLEAN_AUTH, "control", "clean certification for exactly what we asked"),
    # THE headline case, straight out of the FHIR PAS implementation guide.
    "auth_pended_as_complete": (
        {
            **CLEAN_AUTH,
            "determination": "complete",  # PAS `outcome` — REQUIRED field, says success
            "review_action_code": "A4",  # the OPTIONAL field that carries the truth: pended
            "authorization_number": None,
        },
        "very common",
        "top-level status says complete; review action code A4 says PENDED (HL7 PAS's own example)",
    ),
    "auth_reference_not_authorization": (
        {
            **CLEAN_AUTH,
            "determination": None,
            "review_action_code": "A4",
            "authorization_number": "0000123456789012",  # well-formed 16-char tracking number
        },
        "very common",
        "valid-looking 16-char tracking number returned on a pend; passes any format check",
    ),
    "auth_not_required_waiver": (
        {
            "member_id": REQUEST.member_id,
            "authorization_required": False,
            "authorization_number": None,
        },
        "very common",
        "bare 'no auth required' from a payer that delegated MSK to a benefit manager",
    ),
    "auth_denied": (
        {
            **CLEAN_AUTH,
            "determination": "DENIED",
            "review_action_code": "A3",
            "authorization_number": "DENY550210001234",
        },
        "very common",
        "cleanly NOT CERTIFIED (A3) — the agent still 'completed' successfully",
    ),
    "auth_cpt_drift": (
        {**CLEAN_AUTH, "cpt_codes": ["29877"]},
        "very common",
        "certifies 29877 chondroplasty (related, lower-paying, NCCI-bundled) not 29881",
    ),
    "auth_provisional": (
        {**CLEAN_AUTH, "provisional": True, "conditions": ["operative note due within 30 days"]},
        "common",
        "APPROVED + valid number, but provisional/conditional (real policy at some plans)",
    ),
    "auth_caveat_in_notes": (
        {**CLEAN_AUTH, "notes": "Approved pending receipt of the operative note."},
        "common",
        "schema-valid approval whose decisive caveat lives in free text (LLM-payer failure)",
    ),
    "auth_pos_redirect_asc": (
        {**CLEAN_AUTH, "place_of_service": "24"},
        "common",
        "site-of-service redirection 22 (hospital outpatient) -> 24 (ASC)",
    ),
    "auth_date_format_slip": (
        {**CLEAN_AUTH, "effective_date": "07/31/2026", "expiration_date": "07/31/2027"},
        "common",
        "MM/DD/YYYY dates; a string compare would silently pass the lower bound (fail open)",
    ),
    "auth_unknown_determination": (
        {**CLEAN_AUTH, "determination": "REVIEWED", "review_action_code": None},
        "common",
        "determination value outside any known value set -> must fail closed",
    ),
    "auth_non_covered_dx": (
        {**CLEAN_AUTH, "diagnosis_codes": ["M17.11"]},
        "common",
        "certified against a dx CMS NCD 150.9 makes non-covered for knee scope",
    ),
    "auth_dollars_as_cents": (
        {**CLEAN_AUTH, "patient_responsibility_cents": 45_000_00 * 100},
        "unattested",
        "patient responsibility off by 100x (generic integration bug, not payer-attested)",
    ),
    "auth_approved_without_number": (
        {**CLEAN_AUTH, "authorization_number": None},
        "very common",
        "certified but no number was ever generated — nothing to put in box 23",
    ),
    "auth_setting_not_stated": (
        {**CLEAN_AUTH, "place_of_service": None},
        "common",
        "approval omits the setting the claim is adjudicated against",
    ),
    "auth_wrong_rendering_npi": (
        {**CLEAN_AUTH, "rendering_provider_npi": "1043210987"},
        "common",
        "auth bound to a different NPI than the surgeon who will operate",
    ),
    "auth_modifier_stripped": (
        {**CLEAN_AUTH, "laterality": None},
        "common",
        "laterality modifier dropped from the approved procedure",
    ),
    "auth_wrong_laterality": (
        {**CLEAN_AUTH, "laterality": "LT"},
        "unattested",
        "certifies the LEFT knee for a RIGHT knee request (severe if real; no forum case found)",
    ),
    "auth_wrong_member": (
        {**CLEAN_AUTH, "member_id": "W555000111"},
        "long-tail",
        "authorization issued against a different member — HIPAA disclosure, do not forward",
    ),
}

# Legitimate payer variation that MUST NOT be flagged. An over-strict contract gets switched
# off, which is worse than no contract. Several of these were found by researching the actual
# X12/FHIR value sets, not by guessing.
GOOD_VARIATIONS: dict[str, tuple[dict, str]] = {
    "x12_a1_code": (
        {**CLEAN_AUTH, "determination": None, "review_action_code": "A1"},
        "X12 A1 'certified in total' with no free-text determination",
    ),
    "certified_in_total_text": (
        {**CLEAN_AUTH, "determination": "Certified in total", "review_action_code": None},
        "the X12 A1 display string instead of the code",
    ),
    "lowercase_determination": ({**CLEAN_AUTH, "determination": "approved"}, "lowercase"),
    "cpt_superset": (
        {**CLEAN_AUTH, "cpt_codes": ["29881", "29877"]},
        "certifies our code plus an additional one",
    ),
    "effective_on_service_date": (
        {**CLEAN_AUTH, "effective_date": SERVICE_DATE},
        "window opens exactly on the service date",
    ),
    "expires_on_service_date": (
        {**CLEAN_AUTH, "expiration_date": SERVICE_DATE},
        "window closes exactly on the service date",
    ),
    "lowercase_laterality": ({**CLEAN_AUTH, "laterality": "rt"}, "lowercase modifier"),
    "zero_patient_responsibility": (
        {**CLEAN_AUTH, "patient_responsibility_cents": 0},
        "fully covered, nothing owed",
    ),
    "no_patient_responsibility_field": (
        {k: v for k, v in CLEAN_AUTH.items() if k != "patient_responsibility_cents"},
        "payer omits the optional cost-share field",
    ),
    "informational_notes": (
        {**CLEAN_AUTH, "notes": "Certified. Call for questions."},
        "notes present but carrying no caveat",
    ),
    "member_id_echo_padded": (
        {**CLEAN_AUTH, "member_id": f"  {REQUEST.member_id.lower()} "},
        "correct member echoed with different case/padding (a raw == would reject it)",
    ),
    "npi_omitted": (
        {k: v for k, v in CLEAN_AUTH.items() if k != "rendering_provider_npi"},
        "payer omits the optional rendering NPI",
    ),
    "waiver_with_number": (
        {**CLEAN_AUTH, "authorization_required": False},
        "'not required' but substantiated with a real auth number",
    ),
}


class NeverDecides:
    """The most-cited real prior-auth failure: acknowledged, never decided."""

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        return [Progress("received; pending clinical review")]


class PhiOverCollector:
    """HIPAA minimum-necessary: asks for the entire chart when policy needs one fact.

    The medical policy for 29881 requires documentation of failed conservative therapy. Asking
    for 24 months of complete records is over-collection — and the guard has to reason about
    scope, not keyword-match on "SSN".
    """

    ASK = "Upload the patient's complete medical record for the past 24 months to proceed."

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        if turn.index == 0:
            return [NeedInput(self.ASK)]
        return [Complete(result=CLEAN_AUTH)]


class DuplicateOnRetry:
    """Non-idempotent: a retry opens a second case with a different authorization number.

    Uses CLASS-level state on purpose. counterpart gives every task its own behaviour instance
    (so concurrent sessions stay independent), which means per-instance state cannot observe a
    retry — cross-task memory has to be explicit, as here.
    """

    _cases_opened = 0

    def respond(self, turn: Turn, ctx: SessionContext) -> Sequence[Directive]:
        type(self)._cases_opened += 1
        suffix = f"{type(self)._cases_opened:04d}"
        return [Complete(result={**CLEAN_AUTH, "authorization_number": f"AUTH2026881{suffix}"})]


def register_all() -> None:
    for name, (payload, _t, _d) in {**ELIGIBILITY_CASES, **AUTH_CASES}.items():
        register(name, _payer(payload))
    for name, (payload, _d) in GOOD_VARIATIONS.items():
        register(f"ok_{name}", _payer(payload))
    register("auth_never_decides", NeverDecides)
    register("auth_phi_over_collector", PhiOverCollector)
    register("auth_duplicate_on_retry", DuplicateOnRetry)


register_all()


# --- the provider agent ----------------------------------------------------


async def check_eligibility(persona: str, *, guard: bool) -> tuple[bool, str]:
    contract = eligibility_contract(REQUEST) if guard else None
    async with MockAgent(persona).client() as client:
        r = await client.send_message(
            f"271 eligibility: member {REQUEST.member_id}, service type "
            f"{REQUEST.service_type_code}, DOS {REQUEST.service_date}",
            contract=contract,
        )
    if guard and r.contract_violated:
        return False, f"eligibility rejected: {r.report.failures[0].name}"
    payload = r.result if isinstance(r.result, dict) else {}
    # A naive agent asks the one weak question: "does it say active?"
    return bool(payload.get("coverage_active")), "eligibility accepted"


async def request_authorization(persona: str, *, guard: bool) -> tuple[dict | None, str]:
    contract = auth_contract(REQUEST) if guard else None
    async with MockAgent(persona).client() as client:
        r = await client.send_message(
            f"278 review: CPT {REQUEST.cpt_code} {REQUEST.laterality}, dx "
            f"{REQUEST.icd10_code}, POS {REQUEST.place_of_service}, DOS {REQUEST.service_date}",
            contract=contract,
        )
        if r.status == "input-required":
            asked = r.task.status.message.parts[0].text
            if guard and _is_over_collecting(asked):
                return None, "refused: payer over-collected PHI (minimum necessary)"
            r = await client.reply(
                r.task.id,
                "Conservative therapy notes attached.",
                context_id=r.task.context_id,
                contract=contract,
            )
    if r.status != "completed":
        if guard:
            return None, f"no determination: task left in {r.status!r}"
        return {}, f"accepted despite state {r.status!r}"
    if guard and r.contract_violated:
        return None, f"rejected: {r.report.failures[0].name}"
    return (r.result if isinstance(r.result, dict) else {}), "accepted"


def _is_over_collecting(ask: str) -> bool:
    """Scope check: the policy needs failed-conservative-therapy documentation, not the chart."""
    lowered = ask.lower()
    broad = ("complete medical record", "entire record", "all records", "full chart")
    return any(term in lowered for term in broad)


async def clear_procedure(elig: str, auth: str, *, guard: bool) -> dict:
    ok, note = await check_eligibility(elig, guard=guard)
    if not ok:
        return {"decision": "ESCALATE", "why": note}
    determination, auth_note = await request_authorization(auth, guard=guard)
    if determination is None:
        return {"decision": "ESCALATE", "why": auth_note}
    return {
        "decision": "SCHEDULE",
        "why": auth_note,
        "auth_number": determination.get("authorization_number"),
    }


# --- run it ----------------------------------------------------------------


async def run_stage(title: str, cases: dict, *, is_auth: bool) -> dict[str, dict]:
    print(f"\n### {title}")
    print(f"  {'payer agent':30s} {'tier':11s} {'naive':9s} {'guarded':9s} caught by")
    out: dict[str, dict] = {}
    for name, (_payload, tier, desc) in cases.items():
        if is_auth:
            naive = await clear_procedure("elig_clean", name, guard=False)
            guarded = await clear_procedure("elig_clean", name, guard=True)
        else:
            naive = await clear_procedure(name, "auth_clean", guard=False)
            guarded = await clear_procedure(name, "auth_clean", guard=True)
        rule = guarded["why"].split(": ")[-1] if guarded["decision"] == "ESCALATE" else "-"
        print(f"  {name:30s} {tier:11s} {naive['decision']:9s} {guarded['decision']:9s} {rule}")
        print(f"      └ {desc}")
        out[name] = {"naive": naive, "guarded": guarded}
    return out


async def main() -> bool:
    print("=" * 92)
    print("BUSINESS CASE: outpatient prior authorization over A2A (provider <-> payer)")
    print(
        f"  request : CPT {REQUEST.cpt_code} {REQUEST.laterality}, dx {REQUEST.icd10_code}, "
        f"POS {REQUEST.place_of_service}, DOS {REQUEST.service_date}"
    )
    print(
        f"  at risk : ${ALLOWED_AMOUNT_USD:,.2f} per case "
        f"(+${DENIAL_REWORK_USD:.2f} rework per denied claim)"
    )
    print("=" * 92)

    elig = await run_stage(
        "STAGE 1 — payer eligibility agent (X12 271)", ELIGIBILITY_CASES, is_auth=False
    )
    auth = await run_stage(
        "STAGE 2 — payer utilization-management agent (X12 278 / FHIR PAS)",
        AUTH_CASES,
        is_auth=True,
    )

    print("\n### STAGE 3 — behavioural failures (not payload defects)")
    for persona, label in [
        ("auth_never_decides", "never issues a determination (the classic delay)"),
        ("auth_phi_over_collector", "over-collects PHI mid-task (HIPAA minimum necessary)"),
    ]:
        n = await clear_procedure("elig_clean", persona, guard=False)
        g = await clear_procedure("elig_clean", persona, guard=True)
        print(
            f"  {persona:30s} {'behaviour':11s} {n['decision']:9s} {g['decision']:9s} "
            f"{g['why'].split(': ')[-1] if g['decision'] == 'ESCALATE' else '-'}"
        )
        print(f"      └ {label}")

    # Non-idempotent retry: two calls, two different authorization numbers for one event.
    first = await clear_procedure("elig_clean", "auth_duplicate_on_retry", guard=True)
    async with MockAgent("auth_duplicate_on_retry").client() as client:
        a = await client.send_message("278 review", contract=auth_contract(REQUEST))
        b = await client.send_message("278 review (retry)", contract=auth_contract(REQUEST))
    dup = a.result.get("authorization_number") != b.result.get("authorization_number")
    print(
        f"  {'auth_duplicate_on_retry':30s} {'common':11s} {'SCHEDULE':9s} "
        f"{first['decision']:9s} {'two auth numbers' if dup else '-'}"
    )
    print("      └ non-idempotent: a retry opens a second case with a different auth number")

    print("\n### FALSE-POSITIVE HUNT — legitimate payer variation must be ACCEPTED")
    false_positives = []
    for name, (_payload, desc) in GOOD_VARIATIONS.items():
        r = await clear_procedure("elig_clean", f"ok_{name}", guard=True)
        ok = r["decision"] == "SCHEDULE"
        if not ok:
            false_positives.append(name)
        print(f"  {'✅' if ok else '❌ FALSE POSITIVE'} {name:34s} {desc}")

    bad = {k: v for k, v in {**elig, **auth}.items() if not k.endswith("_clean")}
    naive_bad = [k for k, v in bad.items() if v["naive"]["decision"] == "SCHEDULE"]
    guarded_bad = [k for k, v in bad.items() if v["guarded"]["decision"] == "SCHEDULE"]
    controls_ok = all(
        v["guarded"]["decision"] == "SCHEDULE"
        for k, v in {**elig, **auth}.items()
        if k.endswith("_clean")
    )

    print("\n" + "=" * 92)
    print("BUSINESS OUTCOME")
    print(f"  unusable payer answers modelled         : {len(bad)}")
    print(
        f"  naive agent scheduled anyway            : {len(naive_bad)}"
        f"  (${len(naive_bad) * ALLOWED_AMOUNT_USD:,.2f} exposure)"
    )
    print(f"  guarded agent scheduled on a bad answer : {len(guarded_bad)}")
    print(f"  control responses still scheduled       : {'yes' if controls_ok else 'NO'}")
    print(f"  false positives on legitimate variation : {len(false_positives)}")
    print("=" * 92)

    ok = bool(naive_bad) and not guarded_bad and controls_ok and not false_positives
    print(
        "\nVERDICT: every unusable answer caught, no false positives ✅"
        if ok
        else "\nVERDICT: something slipped ❌ — see above"
    )
    return ok


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
