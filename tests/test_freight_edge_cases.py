"""CI coverage for examples/freight_edge_cases.py.

The example is the readable artefact. This asserts the three properties that make it worth
having: every unusable quote is rejected by the rule that should catch it, every legitimate
variation is accepted, and the selection picks the cheapest USABLE quote rather than the
cheapest number.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "freight_edge_cases.py"


def _load() -> Any:
    if (cached := sys.modules.get("freight_edge_cases")) is not None:
        return cached
    spec = importlib.util.spec_from_file_location("freight_edge_cases", EXAMPLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["freight_edge_cases"] = module  # dataclass needs this before exec
    spec.loader.exec_module(module)
    return module


MODULE = _load()


@pytest.mark.parametrize("name", list(MODULE.BAD))
def test_unusable_quote_is_rejected_by_the_expected_rule(name: str) -> None:
    expected = MODULE.BAD[name][1]

    async def run() -> None:
        quote, rules = await MODULE.get_quote(f"fe_bad_{name}", guard=True)
        assert quote is None, f"{name} was accepted"
        # A genuinely bad quote can trip more than one rule; the labelled one must be among them.
        assert expected in rules.split(", "), f"{name}: expected {expected}, got {rules}"

    asyncio.run(run())


@pytest.mark.parametrize("name", list(MODULE.GOOD_VARIATIONS))
def test_legitimate_variation_is_accepted(name: str) -> None:
    async def run() -> None:
        quote, rule = await MODULE.get_quote(f"fe_ok_{name}", guard=True)
        assert quote is not None, f"FALSE POSITIVE on {name}: rejected by {rule}"

    asyncio.run(run())


def test_selection_picks_the_cheapest_usable_quote_not_the_cheapest() -> None:
    """Three of the five bids are cheaper than the answer, and all three are unusable."""

    async def run() -> None:
        naive_carrier, naive_price, _ = await MODULE.tender(guard=False)
        guarded_carrier, guarded_price, _ = await MODULE.tender(guard=True)
        # Unguarded, the winner is the unauthorised carrier, because it is cheapest.
        assert naive_carrier == "Gray Route"
        # Guarded, the winner is the cheapest quote that survives the contract.
        assert guarded_carrier == "Ridgeline Freight"
        assert guarded_price > naive_price  # paying more is the correct outcome here

    asyncio.run(run())


def test_naive_path_accepts_everything() -> None:
    """Without a contract, every one of the unusable quotes is taken."""

    async def run() -> None:
        for name in MODULE.BAD:
            quote, _ = await MODULE.get_quote(f"fe_bad_{name}", guard=False)
            assert quote is not None, f"{name} was skipped even without a contract"

    asyncio.run(run())
