"""CLI entry point for the LLTPM agent."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from lltpm.config import LLTPMConfig
from lltpm.agent import TPMAgent

console = Console()


def _print_md(text: str) -> None:
    console.print(Markdown(text))


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """LLTPM — AI-powered Technical Program Manager agent."""
    ctx.ensure_object(dict)
    config = LLTPMConfig.from_env()
    ctx.obj["config"] = config


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show agent status and loaded integrations."""
    config: LLTPMConfig = ctx.obj["config"]
    agent = TPMAgent(config)

    lines = [
        f"Model: {config.model}",
        f"Data dir: {config.data_dir}",
        f"Tools loaded: {agent.tool_count}",
        "",
        "Integrations:",
        f"  GitHub: {'enabled' if config.github.enabled else 'disabled'}",
        f"  Jira: {'enabled' if config.jira.enabled else 'disabled'}",
        f"  Slack: {'enabled' if config.slack.enabled else 'disabled'}",
        f"  Linear: {'enabled' if config.linear.enabled else 'disabled'}",
        f"  Calendar: {'enabled' if config.calendar.enabled else 'disabled'}",
    ]
    console.print(Panel("\n".join(lines), title="LLTPM Agent Status"))


@cli.command()
@click.argument("message", nargs=-1, required=True)
@click.pass_context
def ask(ctx: click.Context, message: tuple[str, ...]) -> None:
    """Send a one-shot message to the TPM agent."""
    config: LLTPMConfig = ctx.obj["config"]
    agent = TPMAgent(config)

    user_msg = " ".join(message)
    console.print(f"\n[bold]You:[/bold] {user_msg}\n")

    with console.status("[bold green]Thinking..."):
        for turn in agent.run(user_msg):
            if turn.tool_calls:
                for tc in turn.tool_calls:
                    console.print(f"  [dim]tool: {tc['name']}[/dim]")
            if turn.text:
                console.print()
                _print_md(turn.text)


@cli.command()
@click.pass_context
def chat(ctx: click.Context) -> None:
    """Start an interactive chat session with the TPM agent."""
    config: LLTPMConfig = ctx.obj["config"]
    agent = TPMAgent(config)

    console.print(Panel(
        "LLTPM Interactive Mode\n"
        "Type your messages. Use 'quit' or 'exit' to leave.\n"
        "Use 'reset' to clear conversation history.",
        title="LLTPM",
    ))

    while True:
        try:
            user_input = console.input("\n[bold]You:[/bold] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            console.print("Goodbye.")
            break
        if user_input.lower() == "reset":
            agent.reset()
            console.print("[dim]Conversation reset.[/dim]")
            continue

        with console.status("[bold green]Thinking..."):
            for turn in agent.run(user_input):
                if turn.tool_calls:
                    for tc in turn.tool_calls:
                        console.print(f"  [dim]tool: {tc['name']}[/dim]")
                if turn.text:
                    console.print()
                    _print_md(turn.text)


@cli.command()
@click.argument("program_id")
@click.pass_context
def daily(ctx: click.Context, program_id: str) -> None:
    """Run the daily cadence brief for a program."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.daily_cadence import DailyCadence

    store = ArtifactStore(config.ensure_data_dir())
    cadence = DailyCadence(store, config.stale_ticket_threshold_hours)
    brief = cadence.run(program_id)

    _print_md(brief.render())


@cli.command()
@click.argument("program_id")
@click.pass_context
def pulse(ctx: click.Context, program_id: str) -> None:
    """Show the aggregated program pulse."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.update_aggregator import UpdateAggregator

    store = ArtifactStore(config.ensure_data_dir())
    agg = UpdateAggregator(store)
    p = agg.aggregate(program_id)

    _print_md(p.summary())


@cli.command()
@click.argument("program_id")
@click.pass_context
def risks(ctx: click.Context, program_id: str) -> None:
    """Scan and display risk signals for a program."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.risk_engine import RiskEngine

    store = ArtifactStore(config.ensure_data_dir())
    engine = RiskEngine(store)
    signals = engine.scan(program_id)

    if not signals:
        console.print("[green]No risk signals detected.[/green]")
        return

    for s in signals:
        console.print(f"[bold {'red' if s.severity in ('CRITICAL', 'HIGH') else 'yellow'}]"
                       f"[{s.severity}] {s.title}[/bold {'red' if s.severity in ('CRITICAL', 'HIGH') else 'yellow'}]")
        console.print(f"  {s.detail}")
        console.print(f"  [dim]Action: {s.recommended_action}[/dim]")


@cli.command()
@click.pass_context
def today(ctx: click.Context) -> None:
    """Show today's focus based on the weekly cadence."""
    from lltpm.workflows.weekly_planner import WeeklyPlanner
    planner = WeeklyPlanner()
    focus = planner.get_today_focus()

    lines = [f"# Today: {focus['theme']}"]
    for f in focus["focus"]:
        lines.append(f"- {f}")
    _print_md("\n".join(lines))


if __name__ == "__main__":
    cli()
