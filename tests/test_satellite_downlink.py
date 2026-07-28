"""Smoke test for the space-ground-segment scenario (examples/satellite_downlink.py).

The example is the readable artefact; this keeps it honest in CI. It asserts the two
properties that matter — every unusable pass plan is held, and every legitimate oddity
(negative longitude, a zenith pass, a value exactly at a threshold) is still accepted.

Loaded by path because examples/ is deliberately not part of the installed package.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "satellite_downlink.py"


def _load() -> Any:
    """Load the example by path.

    The module must be registered in sys.modules *before* exec_module: its `@dataclass`
    resolves annotations via `sys.modules[cls.__module__]`, which does not exist yet for a
    module loaded this way, and fails with an unhelpful AttributeError.
    """
    if (cached := sys.modules.get("satellite_downlink")) is not None:
        return cached
    spec = importlib.util.spec_from_file_location("satellite_downlink", EXAMPLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["satellite_downlink"] = module
    spec.loader.exec_module(module)
    return module


def test_every_unusable_pass_plan_is_held() -> None:
    m = _load()

    async def run() -> None:
        for name in m.BAD:
            committed, why = await m.schedule(f"gsn_bad_{name}", guard=True)
            assert not committed, f"{name} was committed to the command load"
            assert why, f"{name} held without naming a reason"

    asyncio.run(run())


def test_legitimate_oddities_are_accepted() -> None:
    """No false positives — negative longitude, a zenith pass and threshold-exact values are
    all normal in this domain, and flagging them would get the contract switched off."""
    m = _load()

    async def run() -> None:
        for name in m.GOOD_VARIATIONS:
            committed, why = await m.schedule(f"gsn_ok_{name}", guard=True)
            assert committed, f"FALSE POSITIVE on {name}: {why}"

    asyncio.run(run())


def test_correct_plan_is_committed_and_naive_ops_commit_everything() -> None:
    m = _load()

    async def run() -> None:
        committed, why = await m.schedule("gsn_good", guard=True)
        assert committed, f"control plan was wrongly held: {why}"
        # Without a contract, a bad plan reaches the spacecraft command load.
        naive, _ = await m.schedule("gsn_bad_gps_time_not_utc", guard=False)
        assert naive

    asyncio.run(run())


def test_strict_mode_catches_the_stringified_number() -> None:
    """This scenario opts into strict=True; a number-as-string is a structure failure."""
    m = _load()

    async def run() -> None:
        committed, why = await m.schedule("gsn_bad_stringified_margin", guard=True)
        assert not committed
        assert why == "returns"

    asyncio.run(run())
