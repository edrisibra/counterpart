"""``counterpart`` CLI: ``check`` (conformance smoke report) and ``attack`` (adversarial probes).

    counterpart check  https://my-agent.example.com
    counterpart attack https://my-agent.example.com

Both print a rich table by default, or machine-readable JSON with ``--json`` (for CI).
``check`` is a fast dev-loop smoke report, not the full a2a-tck matrix — see docs.
"""

from __future__ import annotations

import asyncio
import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from counterpart.cli.checks import (
    AttackOutcome,
    CheckOutcome,
    Status,
    run_attacks,
    run_checks,
)

app = typer.Typer(
    name="counterpart",
    help="Test your A2A agent against simulated counterparties, and check/attack a live agent.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_STATUS_STYLE = {
    Status.PASS: "[green]PASS[/green]",
    Status.FAIL: "[red]FAIL[/red]",
    Status.WARN: "[yellow]WARN[/yellow]",
    Status.SKIP: "[dim]SKIP[/dim]",
}


@app.command()
def check(
    url: str = typer.Argument(..., help="Base URL of the A2A agent to check."),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table (for CI)."),
    timeout: float = typer.Option(15.0, help="Per-request timeout in seconds."),
) -> None:
    """Score a live A2A endpoint against spec v1.0 conformance checks."""
    outcomes = asyncio.run(run_checks(url, request_timeout=timeout))
    failures = sum(1 for o in outcomes if o.status is Status.FAIL)
    passes = sum(1 for o in outcomes if o.status is Status.PASS)
    scored = sum(1 for o in outcomes if o.status in (Status.PASS, Status.FAIL))

    if json:
        console.print_json(
            jsonlib.dumps(
                {
                    "url": url,
                    "score": {"passed": passes, "scored": scored, "failures": failures},
                    "checks": [o.as_dict() for o in outcomes],
                }
            )
        )
    else:
        table = Table(title=f"counterpart check — {url}", show_lines=False)
        table.add_column("Check", style="bold")
        table.add_column("Result")
        table.add_column("Spec §", style="cyan")
        table.add_column("Detail", style="dim")
        for o in outcomes:
            table.add_row(o.id, _STATUS_STYLE[o.status], o.spec_section, o.detail)
        console.print(table)
        verdict = (
            "[green]conformant[/green]" if failures == 0 else f"[red]{failures} failure(s)[/red]"
        )
        console.print(f"Score: {passes}/{scored} checks passed — {verdict}")
        console.print("[dim]Smoke report only; run a2a-tck for the full matrix.[/dim]")

    raise typer.Exit(code=1 if failures else 0)


@app.command()
def attack(
    url: str = typer.Argument(..., help="Base URL of the A2A agent to probe."),
    json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table (for CI)."),
    timeout: float = typer.Option(15.0, help="Per-request timeout in seconds."),
) -> None:
    """Send adversarial probes to a live A2A agent and report what came back.

    v0 observes and reports; a true security verdict needs your own contract assertions.
    """
    outcomes = asyncio.run(run_attacks(url, request_timeout=timeout))
    concerning = sum(1 for o in outcomes if o.flag in ("obeyed", "server-error"))

    if json:
        console.print_json(
            jsonlib.dumps(
                {
                    "url": url,
                    "concerning": concerning,
                    "probes": [o.as_dict() for o in outcomes],
                }
            )
        )
    else:
        table = Table(title=f"counterpart attack — {url}", show_lines=True)
        table.add_column("Probe", style="bold")
        table.add_column("Technique", style="cyan")
        table.add_column("Flag")
        table.add_column("Observation", style="dim")
        for o in outcomes:
            flag = {
                "handled": "[green]handled[/green]",
                "obeyed": "[red]obeyed[/red]",
                "server-error": "[red]server-error[/red]",
                "info": "[dim]info[/dim]",
            }.get(o.flag, o.flag)
            table.add_row(o.id, o.technique, flag, o.observation)
        console.print(table)
        if concerning:
            console.print(f"[red]{concerning} probe(s) need attention.[/red]")
        else:
            console.print("[green]No probe was obeyed or caused a server error.[/green]")
        console.print(
            "[dim]Probes observe behaviour; confirm defences with your own contracts.[/dim]"
        )

    raise typer.Exit(code=1 if concerning else 0)


# Re-exported for the console_scripts entry point and tests.
__all__ = ["AttackOutcome", "CheckOutcome", "app", "attack", "check"]
