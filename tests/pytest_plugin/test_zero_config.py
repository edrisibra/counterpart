"""The quickstart must work with ZERO configuration — no conftest, no ini options.

This is the first-five-minutes promise. Before this was fixed, the README quickstart failed for
every new user with "async def functions are not natively supported", because the repo's own
pyproject set `asyncio_mode = "auto"` while a stranger's project would not. The plugin now
supplies that default itself.

These tests run pytest in a subprocess via `pytester`, so they check what a real user's project
sees rather than what this repo's config happens to provide.
"""

import pytest

pytest_plugins = ["pytester"]

QUICKSTART = """
from pydantic import BaseModel
from counterpart import Contract


class Quote(BaseModel):
    price: float
    currency: str


async def test_lying_peer_is_caught(mock_agent):
    contract = (
        Contract("freight quote")
        .returns(Quote)
        .require("price_is_number", lambda q: isinstance(q.price, (int, float)))
        .expect_status("completed")
    )
    peer = mock_agent(persona="false_success")
    async with peer.client() as client:
        task = await client.send_message("Quote 2 pallets LA->Dallas", contract=contract)
    assert task.status == "completed"
    assert task.contract_violated
"""


def test_quickstart_works_with_no_configuration(pytester: pytest.Pytester) -> None:
    """A bare `async def` test passes with no conftest.py and no ini settings."""
    pytester.makepyfile(test_quickstart=QUICKSTART)
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)


def test_explicit_strict_mode_is_respected(pytester: pytest.Pytester) -> None:
    """We fill in a default; we never override the user's explicit choice.

    Under `asyncio_mode = strict` a bare async test is *supposed* to fail, so this asserts our
    default did not hijack the setting.
    """
    pytester.makepyfile(test_quickstart=QUICKSTART)
    pytester.makefile(".ini", pytest="[pytest]\nasyncio_mode = strict\n")
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=0, failed=1)


def test_mock_agent_fixture_is_available_without_import(pytester: pytest.Pytester) -> None:
    """Installing the package is the whole setup — the fixture is registered via entry point."""
    pytester.makepyfile(
        test_fixture_present="""
        def test_fixture_is_injected(mock_agent):
            agent = mock_agent(persona="cooperative")
            assert agent.persona_name == "cooperative"
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)
