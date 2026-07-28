"""Contract assertions — verify a delegated *result*, not just the protocol.

This is a2a-sandbox's centerpiece and it is deliberately protocol-agnostic: a ``Contract``
verifies an arbitrary result payload (a dict, a value, whatever a protocol adapter hands
it) against an expected structural shape plus predicates, and records the counterparty's
self-reported status. The pairing of "claimed success" with "checks failed" is what makes
silent partial completion — a peer that reports ``completed`` while returning incomplete or
corrupt output — a single, legible assertion instead of a debugging session.

The shape is a trimmed version of the delegation contract in Prakash, "The Provenance
Paradox in Multi-Agent LLM Routing" (arXiv:2603.18043): we keep the parts that make a
result *checkable* (a structural receipt + machine-verifiable predicates + a typed failure)
and drop the parts the paper itself concedes it cannot enforce (free-form success criteria,
self-reported token budgets). See docs/spec-notes.md and docs/roadmap.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

_UNSET = object()

ReceiptT = TypeVar("ReceiptT")

# A predicate runs against the parsed receipt and returns True when satisfied. It may also
# raise or return a non-bool; both are treated as failure with the detail captured.
Predicate = Callable[[Any], bool]


class FailureCategory(StrEnum):
    """Typed failure categories (adapted from the LDP paper's typed failures).

    Kept to the three a contract can actually determine from a returned result. All three
    are non-retryable contract violations — rerouting to another peer, not retrying, is the
    correct response (per the paper's ``policy`` category).
    """

    STRUCTURE = "structure"  # result did not match the expected shape (no usable receipt)
    STATUS = "status"  # reported status did not match what the contract expected
    PREDICATE = "predicate"  # a required predicate over the receipt failed


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check within a contract."""

    name: str
    passed: bool
    category: FailureCategory
    detail: str = ""


@dataclass
class ContractReport(Generic[ReceiptT]):
    """The verifiable receipt: what the peer claimed, and whether the result held up."""

    objective: str
    reported_status: str | None
    receipt: ReceiptT | None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def contract_violated(self) -> bool:
        """True if any check failed — i.e. the delegated work is not acceptable."""
        return bool(self.failures)

    @property
    def satisfied(self) -> bool:
        return not self.contract_violated

    @property
    def typed_failure(self) -> FailureCategory | None:
        """The category of the first failure (structure > status > predicate order of report)."""
        failures = self.failures
        return failures[0].category if failures else None

    def summary(self) -> str:
        if self.satisfied:
            n = len(self.checks)
            return f"contract satisfied: {self.objective!r} ({n} checks passed)"
        header = (
            f"contract VIOLATED: {self.objective!r} (reported status: {self.reported_status!r})"
        )
        lines = [header]
        lines += [f"  - [{c.category}] {c.name}: {c.detail}" for c in self.failures]
        return "\n".join(lines)

    def __bool__(self) -> bool:
        return self.satisfied


@dataclass(frozen=True)
class _Requirement:
    name: str
    predicate: Predicate


class Contract(Generic[ReceiptT]):
    """Declare what a correct delegated result must satisfy, then :meth:`verify` a result.

    Built fluently::

        contract = (Contract("freight quote")
            .returns(Quote)                                    # structural receipt
            .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
            .expect_status("completed"))

        report = contract.verify(result=peer_payload, reported_status="completed")
        assert report.contract_violated          # said done, returned garbage -> caught

    ``returns`` accepts a Pydantic model or any type usable with ``TypeAdapter`` (e.g. a
    ``TypedDict`` or ``dict[str, float]``). Predicates run only if the structural check
    passed — there is no point asserting on a receipt that never parsed.
    """

    def __init__(self, objective: str) -> None:
        self.objective = objective
        self._model: Any = None
        self._adapter: TypeAdapter[Any] | None = None
        self._requirements: list[_Requirement] = []
        self._expected_status: Any = _UNSET

    def returns(self, shape: type[ReceiptT] | Any) -> Contract[ReceiptT]:
        """Declare the structural receipt: the returned result MUST parse into this shape.

        Passing ``None`` raises rather than silently disabling the check. ``.returns(None)``
        reads like "returns nothing" but would previously install no shape at all, so the
        contract accepted any payload and reported ``satisfied`` — a check that silently
        passes everything is worse than no check, because the caller believes it ran. To
        deliberately skip structural validation, simply do not call ``returns()``.
        """
        if shape is None:
            raise TypeError(
                "Contract.returns(None) would disable structural validation while still "
                "reporting success. Pass a model/type, or omit returns() entirely to check "
                "only predicates."
            )
        self._model = shape
        if not (isinstance(shape, type) and issubclass(shape, BaseModel)):
            self._adapter = TypeAdapter(shape)
        return self

    def require(self, name: str, predicate: Predicate) -> Contract[ReceiptT]:
        """Add a machine-verifiable predicate over the parsed receipt."""
        self._requirements.append(_Requirement(name, predicate))
        return self

    def expect_status(self, status: str) -> Contract[ReceiptT]:
        """Declare the status the peer is expected to report on success."""
        self._expected_status = status
        return self

    def _parse(self, result: Any) -> tuple[Any, CheckResult | None]:
        if self._model is None:
            return result, None
        try:
            if isinstance(self._model, type) and issubclass(self._model, BaseModel):
                receipt = self._model.model_validate(result)
            else:
                assert self._adapter is not None
                receipt = self._adapter.validate_python(result)
        except ValidationError as exc:
            name = getattr(self._model, "__name__", str(self._model))
            errors = exc.errors()
            detail = (
                f"result did not match {name}: {len(errors)} error(s); first: {errors[0]['msg']!r}"
            )
            return None, CheckResult("returns", False, FailureCategory.STRUCTURE, detail)
        except Exception as exc:
            detail = f"parsing raised {type(exc).__name__}: {exc}"
            return None, CheckResult("returns", False, FailureCategory.STRUCTURE, detail)
        return receipt, CheckResult("returns", True, FailureCategory.STRUCTURE)

    def verify(
        self, *, result: Any, reported_status: str | None = None
    ) -> ContractReport[ReceiptT]:
        """Verify a delegated ``result`` (and the peer's ``reported_status``) against this
        contract. Never raises for a failed check — the outcome lives in the report."""
        checks: list[CheckResult] = []

        receipt, structure_check = self._parse(result)
        if structure_check is not None:
            checks.append(structure_check)

        if self._expected_status is not _UNSET:
            ok = reported_status == self._expected_status
            checks.append(
                CheckResult(
                    "expect_status",
                    ok,
                    FailureCategory.STATUS,
                    ""
                    if ok
                    else f"expected status {self._expected_status!r}, got {reported_status!r}",
                )
            )

        structure_ok = structure_check is None or structure_check.passed
        for req in self._requirements:
            if not structure_ok:
                checks.append(
                    CheckResult(
                        req.name,
                        False,
                        FailureCategory.PREDICATE,
                        "not evaluated: result did not match the expected shape",
                    )
                )
                continue
            checks.append(self._run_predicate(req, receipt))

        return ContractReport(
            objective=self.objective,
            reported_status=reported_status,
            receipt=receipt if structure_ok else None,
            checks=checks,
        )

    @staticmethod
    def _run_predicate(req: _Requirement, receipt: Any) -> CheckResult:
        try:
            outcome = req.predicate(receipt)
        except Exception as exc:  # a predicate that blows up is a failed check, not a crash
            detail = f"predicate raised {type(exc).__name__}: {exc}"
            return CheckResult(req.name, False, FailureCategory.PREDICATE, detail)
        if outcome is True:
            return CheckResult(req.name, True, FailureCategory.PREDICATE)
        return CheckResult(
            req.name, False, FailureCategory.PREDICATE, f"predicate returned {outcome!r}"
        )
