"""CLI tests: run check/attack against a real served MockAgent, and via the Typer runner."""

import json

from typer.testing import CliRunner

from counterpart import MockAgent, wrap
from counterpart.adapters.a2a.mockagent import serve_asgi
from counterpart.cli.checks import Status, run_attacks, run_checks
from counterpart.cli.main import app

runner = CliRunner()


async def test_check_scores_a_conformant_mock_high() -> None:
    """A cooperative MockAgent is a spec-valid A2A server, so check should mostly pass."""
    mock = MockAgent("cooperative", result={"ok": True})
    with mock.serve() as url:
        outcomes = await run_checks(url)
    by_id = {o.id: o for o in outcomes}
    assert by_id["agent_card_reachable"].status is Status.PASS
    assert by_id["agent_card_valid"].status is Status.PASS
    assert by_id["send_message"].status is Status.PASS
    assert by_id["method_not_found"].status is Status.PASS
    assert by_id["task_not_found"].status is Status.PASS
    assert by_id["streaming_honesty"].status is Status.PASS
    # No hard failures against our own conformant server.
    assert not [o for o in outcomes if o.status is Status.FAIL]


async def test_attack_on_a_naive_echo_agent_flags_injection() -> None:
    """An agent that echoes its input 'obeys' the injection probe."""

    def echo(text: str) -> dict[str, str]:
        return {"echo": text}

    with serve_asgi(wrap(echo, name="echo-agent")) as url:
        outcomes = await run_attacks(url)
    by_id = {o.id: o for o in outcomes}
    assert by_id["prompt_injection"].flag == "obeyed"  # echo agent parroted the token
    assert by_id["malformed_request"].flag == "handled"  # our server returns a JSON-RPC error


def test_check_command_json_output_via_runner() -> None:
    served = MockAgent("cooperative", result={"ok": True})
    with served.serve() as url:
        result = runner.invoke(app, ["check", url, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["url"] == url
    assert payload["score"]["failures"] == 0
    assert any(c["id"] == "send_message" for c in payload["checks"])


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help exits with Click's usage code (2) while still printing the commands.
    assert result.exit_code == 2
    assert "check" in result.stdout and "attack" in result.stdout
