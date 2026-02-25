"""CLI entry point for the LLTPM agent."""

from __future__ import annotations

import sys
from pathlib import Path

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


# ── Init & Onboarding ────────────────────────────────────


@cli.command()
@click.option("--env-file", type=click.Path(), default=".env", help="Path for .env output")
@click.pass_context
def init(ctx: click.Context, env_file: str) -> None:
    """Interactive setup wizard — configure integrations and credentials."""
    from lltpm.cli import run_config_wizard, save_env_file, save_json_config

    config: LLTPMConfig = ctx.obj["config"]
    env_vars = run_config_wizard(config.data_dir)

    save_env_file(env_vars, Path(env_file))
    data_dir = Path(env_vars.get("LLTPM_DATA_DIR", str(config.data_dir)))
    save_json_config(env_vars, data_dir)

    console.print("\n[green]Setup complete![/green] Run [bold]lltpm status[/bold] to verify.")


@cli.command()
@click.pass_context
def welcome(ctx: click.Context) -> None:
    """Show the onboarding guide and setup status."""
    from lltpm.cli import run_onboarding

    config: LLTPMConfig = ctx.obj["config"]
    run_onboarding(config)


# ── Status & Info ─────────────────────────────────────────


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show agent status and loaded integrations."""
    from lltpm.cli import display_integrations_table

    config: LLTPMConfig = ctx.obj["config"]
    agent = TPMAgent(config)

    console.print(Panel(
        f"Model: {config.model}\n"
        f"Data dir: {config.data_dir}\n"
        f"Tools loaded: {agent.tool_count}",
        title="LLTPM Agent Status",
        border_style="blue",
    ))
    console.print()
    display_integrations_table(config)


@cli.command()
@click.pass_context
def tools(ctx: click.Context) -> None:
    """List all available tools."""
    from lltpm.cli import display_tool_table

    config: LLTPMConfig = ctx.obj["config"]
    agent = TPMAgent(config)
    display_tool_table(agent._registry.definitions)


# ── Interactive Modes ─────────────────────────────────────


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
@click.option("--program", "-p", default="", help="Auto-set program context")
@click.pass_context
def chat(ctx: click.Context, program: str) -> None:
    """Start an interactive chat session with the TPM agent."""
    config: LLTPMConfig = ctx.obj["config"]
    agent = TPMAgent(config)

    console.print(Panel(
        "[bold]LLTPM Interactive Mode[/bold]\n\n"
        "Commands:\n"
        "  [dim]quit/exit[/dim]  — Leave the session\n"
        "  [dim]reset[/dim]     — Clear conversation history\n"
        "  [dim]tools[/dim]     — List available tools\n"
        "  [dim]export[/dim]    — Export conversation to markdown\n"
        "  [dim]status[/dim]    — Show agent status",
        title="LLTPM Chat",
        border_style="cyan",
    ))

    # Set program context if provided
    if program:
        console.print(f"[dim]Program context: {program}[/dim]")
        # Silently prime the agent with program context
        agent._messages.append({
            "role": "user",
            "content": f"I'm working on program '{program}'. Keep this context for our conversation.",
        })
        agent._messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": f"Understood, I'll focus on program '{program}'."}],
        })

    conversation_log: list[dict[str, str]] = []

    while True:
        try:
            user_input = console.input("\n[bold]You:[/bold] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye.")
            break

        if not user_input:
            continue

        lower = user_input.lower()
        if lower in ("quit", "exit"):
            console.print("Goodbye.")
            break
        if lower == "reset":
            agent.reset()
            conversation_log.clear()
            console.print("[dim]Conversation reset.[/dim]")
            continue
        if lower == "tools":
            from lltpm.cli import display_tool_table
            display_tool_table(agent._registry.definitions)
            continue
        if lower == "export":
            _export_conversation(conversation_log)
            continue
        if lower == "status":
            console.print(f"[dim]Tools: {agent.tool_count} | Messages: {len(agent._messages)}[/dim]")
            continue

        conversation_log.append({"role": "user", "text": user_input})

        with console.status("[bold green]Thinking..."):
            for turn in agent.run(user_input):
                if turn.tool_calls:
                    for tc in turn.tool_calls:
                        console.print(f"  [dim]tool: {tc['name']}[/dim]")
                if turn.text:
                    console.print()
                    _print_md(turn.text)
                    conversation_log.append({"role": "assistant", "text": turn.text})


def _export_conversation(log: list[dict[str, str]]) -> None:
    """Export conversation to a markdown file."""
    if not log:
        console.print("[dim]No conversation to export.[/dim]")
        return

    lines = ["# LLTPM Chat Export\n"]
    for entry in log:
        role = "**You**" if entry["role"] == "user" else "**LLTPM**"
        lines.append(f"{role}: {entry['text']}\n")

    path = Path("lltpm-chat-export.md")
    path.write_text("\n".join(lines))
    console.print(f"[green]Exported to {path}[/green]")


# ── Program Commands ──────────────────────────────────────


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
        color = "red" if s.severity in ("CRITICAL", "HIGH") else "yellow"
        console.print(f"[bold {color}][{s.severity}] {s.title}[/bold {color}]")
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


# ── Portfolio & Analysis Commands ─────────────────────────


@cli.command()
@click.pass_context
def portfolio(ctx: click.Context) -> None:
    """Show cross-program portfolio overview."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.cross_program_intel import CrossProgramIntel

    store = ArtifactStore(config.ensure_data_dir())
    intel = CrossProgramIntel(store)
    view = intel.generate_portfolio_view()

    _print_md(view.summary())


@cli.command()
@click.argument("program_id")
@click.pass_context
def predict(ctx: click.Context, program_id: str) -> None:
    """Run risk prediction for a program."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.risk_predictor import RiskPredictor

    store = ArtifactStore(config.ensure_data_dir())
    predictor = RiskPredictor(store)
    predictions = predictor.predict_all(program_id)

    if not predictions:
        console.print("[green]No risk predictions.[/green]")
        return

    for p in predictions:
        console.print(f"[bold yellow][{p.risk_type}] {p.title}[/bold yellow]")
        console.print(f"  Probability: {p.probability:.0%} | Impact: {p.impact}")
        console.print(f"  [dim]{p.recommendation}[/dim]")


@cli.command()
@click.argument("program_id")
@click.option("--format", "fmt", type=click.Choice(["html", "markdown"]), default="markdown")
@click.pass_context
def report(ctx: click.Context, program_id: str, fmt: str) -> None:
    """Generate a full program report."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.document_generator import DocumentGenerator

    store = ArtifactStore(config.ensure_data_dir())
    gen = DocumentGenerator(store)
    output = gen.render_full_report(program_id, fmt)

    if fmt == "html":
        path = Path(f"{program_id}-report.html")
        path.write_text(output)
        console.print(f"[green]Report saved to {path}[/green]")
    else:
        _print_md(output)


@cli.command()
@click.argument("program_id")
@click.pass_context
def trends(ctx: click.Context, program_id: str) -> None:
    """Show trend analysis for a program."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.trend_analyzer import TrendAnalyzer

    store = ArtifactStore(config.ensure_data_dir())
    analyzer = TrendAnalyzer(store)
    trend_report = analyzer.analyze_trends(program_id)

    _print_md(trend_report.summary())


@cli.command()
@click.argument("program_id")
@click.pass_context
def approvals(ctx: click.Context, program_id: str) -> None:
    """Show pending approvals for a program."""
    config: LLTPMConfig = ctx.obj["config"]

    from lltpm.artifacts.store import ArtifactStore
    from lltpm.workflows.approval_engine import ApprovalEngine

    store = ArtifactStore(config.ensure_data_dir())
    engine = ApprovalEngine(store)

    _print_md(engine.get_summary(program_id))


@cli.command()
@click.pass_context
def dashboard(ctx: click.Context) -> None:
    """Launch the web dashboard (requires dashboard extras)."""
    try:
        import uvicorn
        from lltpm.dashboard import create_app

        config: LLTPMConfig = ctx.obj["config"]
        console.print("[bold]Starting LLTPM Dashboard...[/bold]")
        console.print("[dim]http://localhost:8000/docs for API docs[/dim]")

        app = create_app(config)
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except ImportError:
        console.print(
            "[red]Dashboard requires extra dependencies.[/red]\n"
            "Install with: pip install lltpm[dashboard]"
        )


if __name__ == "__main__":
    cli()
