"""Typer command-line interface for converge-flights.

Usage::

    converge-flights search --config config.yaml --out results.xlsx

Loads ``.env`` for API credentials, resolves date windows, runs the selected
provider(s), prints a ranked comparison table with rich, and writes an
``.xlsx`` workbook. Optionally dumps raw provider JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from converge_flights.cache import QueryCache
from converge_flights.compare import compare
from converge_flights.config import Config
from converge_flights.dates import build_windows
from converge_flights.export import export_report
from converge_flights.models import ComparisonReport
from converge_flights.providers import build_providers
from converge_flights.providers.base import MissingCredentialsError, ProviderError

app = typer.Typer(
    add_completion=False,
    help="Find and compare group flights to a shared destination.",
)
console = Console()


@app.callback()
def _root() -> None:
    """Find and compare group flights to a shared destination.

    A no-op callback that forces typer to expose ``search`` as a named
    subcommand (``converge-flights search ...``) rather than collapsing the
    single command into the top level.
    """


def render_table(report: ComparisonReport, traveler_names: list[str]) -> Table:
    """Build a rich table of windows ranked by group total."""
    table = Table(
        title=f"Group flight comparison ({report.currency}) — cheapest first",
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("#", justify="right")
    table.add_column("Depart")
    table.add_column("Return")
    table.add_column("Group total", justify="right")
    for name in traveler_names:
        table.add_column(name, justify="right")
    table.add_column("Notes", style="yellow")

    for rank, window in enumerate(report.ranked(), start=1):
        total = window.group_total
        total_str = f"[bold green]{total:,.2f}[/]" if total is not None else "[red]—[/]"
        cells = [
            str(rank),
            window.window.depart.isoformat(),
            window.window.return_.isoformat(),
            total_str,
        ]
        for name in traveler_names:
            result = window.per_traveler.get(name)
            offer = result.best_offer if result else None
            if offer is None:
                cells.append("[red]none[/]")
            else:
                cells.append(f"{offer.price:,.0f} {offer.provider}/{offer.origin}")
        if window.missing:
            note = "no option: " + ", ".join(window.missing)
        else:
            note = ""
        cells.append(note)
        table.add_row(*cells)
    return table


def _dump_raw(raw_sinks: dict[str, list[dict[str, object]]], out: Path) -> list[Path]:
    """Write each provider's raw request/response pairs to JSON files."""
    written: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    for provider, records in raw_sinks.items():
        path = out / f"raw_{provider}.json"
        path.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        written.append(path)
    return written


@app.command()
def search(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            exists=True,
            dir_okay=False,
            help="Path to config.yaml.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Path for the .xlsx export."),
    ] = Path("results.xlsx"),
    dump: Annotated[
        Path | None,
        typer.Option("--dump", help="Directory to write raw provider JSON to."),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Bypass the on-disk cache for this run."),
    ] = False,
) -> None:
    """Search fares for every traveler and export a ranked comparison."""
    load_dotenv()

    try:
        cfg = Config.from_yaml(config)
    except Exception as exc:  # config/validation error -> friendly message
        console.print(f"[red]Invalid config:[/] {exc}")
        raise typer.Exit(code=2) from exc

    cache_cfg = cfg.cache
    if no_cache:
        cache_cfg = cache_cfg.model_copy(update={"enabled": False})
    cache = QueryCache(cache_cfg)

    windows = build_windows(cfg.date_windows)
    if not windows:
        console.print("[red]No date windows resolved from config.[/]")
        raise typer.Exit(code=1)

    raw_sinks: dict[str, list[dict[str, object]]] | None = (
        {} if (dump is not None or cfg.raw_dump) else None
    )

    try:
        providers = build_providers(cfg, cache=cache, raw_sinks=raw_sinks)
        traveler_names = [t.name for t in cfg.travelers]
        console.print(
            f"Searching [bold]{len(windows)}[/] windows × "
            f"[bold]{len(traveler_names)}[/] travelers via "
            f"[bold]{cfg.provider.value}[/] …"
        )
        report = compare(providers, windows, cfg)
    except MissingCredentialsError as exc:
        console.print(f"[red]Missing credentials:[/] {exc}")
        raise typer.Exit(code=3) from exc
    except ProviderError as exc:
        console.print(f"[red]Provider error:[/] {exc}")
        raise typer.Exit(code=4) from exc

    console.print(render_table(report, traveler_names))

    export_path = export_report(report, traveler_names, out)
    console.print(f"[green]Wrote workbook:[/] {export_path}")

    if raw_sinks is not None:
        dump_dir = dump if dump is not None else out.parent / "raw"
        written = _dump_raw(raw_sinks, dump_dir)
        for path in written:
            console.print(f"[green]Wrote raw dump:[/] {path}")

    incomplete = [w for w in report.windows if not w.complete]
    if incomplete:
        console.print(
            f"[yellow]{len(incomplete)} window(s) had a traveler with no "
            f"qualifying option — see the 'Notes' column.[/]"
        )


def main() -> None:  # pragma: no cover - entrypoint shim
    """Console-script entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
