"""Protocol-agnostic core: task lifecycle, result-verifying contracts, personas/behaviour.

Hard rule: nothing in this package may import from, or assume the existence of, any concrete
protocol (A2A or otherwise). Protocol bindings live in ``a2a_sandbox.adapters``. A test
(``tests/core/test_core_is_protocol_free.py``) enforces this.
"""

from a2a_sandbox.core.behaviour import (
    Behaviour,
    Complete,
    Deliver,
    Directive,
    Drop,
    EmitRawStatus,
    Fail,
    NeedInput,
    Progress,
    SessionContext,
    Turn,
    Wait,
    run_behaviour,
)
from a2a_sandbox.core.contract import (
    CheckResult,
    Contract,
    ContractReport,
    FailureCategory,
    Predicate,
)
from a2a_sandbox.core.lifecycle import (
    IllegalTransition,
    Lifecycle,
    LifecycleSpec,
    TimelineEntry,
)

__all__ = [
    "Behaviour",
    "CheckResult",
    "Complete",
    "Contract",
    "ContractReport",
    "Deliver",
    "Directive",
    "Drop",
    "EmitRawStatus",
    "Fail",
    "FailureCategory",
    "IllegalTransition",
    "Lifecycle",
    "LifecycleSpec",
    "NeedInput",
    "Predicate",
    "Progress",
    "SessionContext",
    "TimelineEntry",
    "Turn",
    "Wait",
    "run_behaviour",
]
