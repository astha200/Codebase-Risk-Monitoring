from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .aggregator import assess_commit, assess_many
from .config import settings
from .ingestor import iter_commits
from .store import (
    commit_already_scored,
    connect,
    get_report,
    list_reports,
    save_report,
    upsert_commit,
)

app = typer.Typer(help="Codebase risk monitor — score and rank commits by risk.")
console = Console()


def _band_color(band: str) -> str:
    return {"low": "green", "medium": "yellow", "high": "red", "critical": "bold red"}.get(band, "white")


@app.command()
def scan(
    repo: Path = typer.Argument(..., help="Path to local git repo"),
    since: str = typer.Option("HEAD~20", help="Git rev range, e.g. HEAD~50 or main..feature"),
    limit: Optional[int] = typer.Option(None, help="Max commits to scan"),
    force: bool = typer.Option(False, "--force", help="Re-score even if cached"),
    concurrency: int = typer.Option(3, help="Parallel LLM calls"),
):
    """Scan commits in a local repo and store risk reports."""
    repo = repo.expanduser().resolve()
    if not (repo / ".git").exists():
        console.print(f"[red]Not a git repo:[/red] {repo}")
        raise typer.Exit(1)

    if not settings.active_api_key:
        console.print(f"[red]API key missing for provider '{settings.provider}'.[/red] Set it in .env.")
        raise typer.Exit(1)

    commits = list(iter_commits(repo, since=since, limit=limit))
    console.print(f"[cyan]Ingested {len(commits)} commits from[/cyan] {repo}")

    with connect() as conn:
        to_score = []
        for c in commits:
            upsert_commit(conn, str(repo), c)
            if force or not commit_already_scored(
                conn, c.sha, "v1",
                f"specialist={settings.model_specialist};judge={settings.model_judge}",
            ):
                to_score.append(c)

        if not to_score:
            console.print("[green]All commits already scored. Use --force to re-score.[/green]")
            _print_table(list_reports(conn, limit=len(commits)))
            return

        console.print(f"[cyan]Scoring {len(to_score)} commits (concurrency={concurrency})...[/cyan]")
        reports = asyncio.run(assess_many(to_score, concurrency=concurrency))
        for r in reports:
            save_report(conn, r)
        _print_table(list_reports(conn, limit=len(commits)))


def _print_table(reports):
    table = Table(title="Commit risk ranking", show_lines=False)
    table.add_column("SHA", style="dim")
    table.add_column("Score", justify="right")
    table.add_column("Band")
    table.add_column("Action")
    table.add_column("Summary", overflow="fold")
    for r in reports:
        table.add_row(
            r.sha[:8],
            str(r.risk_score),
            f"[{_band_color(r.risk_band.value)}]{r.risk_band.value.upper()}[/]",
            r.recommended_action,
            escape(r.summary[:160]),
        )
    console.print(table)


@app.command()
def explain(sha: str = typer.Argument(..., help="Commit SHA (full or prefix)")):
    """Print full breakdown for a single commit."""
    with connect() as conn:
        # Resolve prefix
        row = conn.execute("SELECT sha FROM reports WHERE sha LIKE ? LIMIT 1", (sha + "%",)).fetchone()
        if not row:
            console.print(f"[red]No report found for[/red] {sha}")
            raise typer.Exit(1)
        report = get_report(conn, row["sha"])

    console.print(f"\n[bold]Commit {report.sha[:12]}[/bold]")
    console.print(f"Score: [bold {_band_color(report.risk_band.value)}]{report.risk_score}[/] ({report.risk_band.value})")
    console.print(f"Action: {report.recommended_action}")
    console.print(f"Summary: {report.summary}\n")

    if report.skipped_deep_analysis:
        console.print(f"[yellow]Deep analysis skipped: {report.skipped_reason}[/yellow]")
        console.print(json.dumps(report.triage_signals.model_dump(), indent=2))
        return

    table = Table(title="Dimensions")
    table.add_column("Dimension")
    table.add_column("Score", justify="right")
    table.add_column("Conf", justify="right")
    table.add_column("Rationale", overflow="fold")
    table.add_column("Evidence", overflow="fold")
    for d in report.dimensions:
        evidence = "\n".join(f"{e.file}:{e.line}" if e.line else e.file for e in d.evidence) or "—"
        table.add_row(d.dimension.value, str(d.score), f"{d.confidence:.2f}", d.rationale, evidence)
    console.print(table)


@app.command(name="list")
def list_cmd(limit: int = typer.Option(20)):
    """List previously scored commits, highest risk first."""
    with connect() as conn:
        _print_table(list_reports(conn, limit=limit))


@app.command()
def dashboard(port: int = typer.Option(8501)):
    """Launch the Streamlit dashboard."""
    script = Path(__file__).parent / "dashboard.py"
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(script), "--server.port", str(port)],
        check=False,
    )


if __name__ == "__main__":
    app()
