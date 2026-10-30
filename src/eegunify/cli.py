"""Command-line interface (``eegunify``).

Exit codes: 0 ok, 1 a recording failed, 2 config/usage error, 3 reader error.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from eegunify import __version__

app = typer.Typer(
    name="eegunify",
    help="Convert raw EEG recordings (any vendor format) into canonical BIDS-EEG.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Validate configs and print their schema.", no_args_is_help=True)
app.add_typer(config_app, name="config")
console = Console()

EXIT_FAILED, EXIT_USAGE, EXIT_READER = 1, 2, 3


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Errors only.")] = False,
) -> None:
    """eegunify: one canonical format for every raw EEG recording."""
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.INFO
    logging.basicConfig(
        level=level, format="%(message)s", handlers=[RichHandler(console=console)], force=True
    )
    logging.getLogger("mne").setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """Print the eegunify, MNE and MNE-BIDS versions."""
    import mne
    import mne_bids

    console.print(f"eegunify {__version__}")
    console.print(f"mne      {mne.__version__}")
    console.print(f"mne-bids {mne_bids.__version__}")


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(exists=True, help="Recording to inspect.")],
    fmt: Annotated[str | None, typer.Option("--format", help="Override detection.")] = None,
) -> None:
    """Read one recording and summarise channels, sampling rate, events and checks. No writes."""
    from eegunify.canonical import CanonicalError
    from eegunify.detect import UnknownFormatError
    from eegunify.pipeline import default_config, read_recording

    cfg = default_config(path)
    if fmt:
        cfg.format.name = fmt  # type: ignore[assignment]
    try:
        rec, warnings = read_recording(path, cfg)
    except UnknownFormatError as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    except CanonicalError as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_FAILED) from err
    except Exception as err:
        console.print(f"[red]could not read {path}: {type(err).__name__}: {err}")
        raise typer.Exit(EXIT_READER) from err

    raw = rec.raw
    console.print(
        f"[bold]{path.name}[/]  format=[cyan]{rec.format}[/]  "
        f"sfreq={raw.info['sfreq']:g} Hz  duration={raw.times[-1]:.1f} s"
    )
    types: dict[str, list[str]] = {}
    for name, t in zip(raw.ch_names, raw.get_channel_types(), strict=True):
        types.setdefault(t, []).append(name)
    for t, names in sorted(types.items()):
        console.print(
            f"  {t:5s} {len(names):3d}: {', '.join(names[:12])}"
            + (" ..." if len(names) > 12 else "")
        )
    renamed = {o: n for o, n in rec.rename_log.items() if o != n}
    if renamed:
        console.print(
            f"  renamed {len(renamed)}: "
            + ", ".join(f"{o}->{n}" for o, n in list(renamed.items())[:8])
            + (" ..." if len(renamed) > 8 else "")
        )
    console.print(f"  units: {rec.units.explanation}")
    console.print(f"  reference: {rec.reference}")
    counts = rec.events.table["trial_type"].value_counts()
    table = Table("trial_type", "n", title=f"events ({rec.events.method})")
    for tt, n in counts.items():
        table.add_row(str(tt), str(n))
    console.print(table)
    for w in warnings:
        console.print(f"  [yellow]warning[/] [rule {w.rule}] {w.message}")


def _run(config: Path, subject: list[str] | None, root: Path, **kwargs: bool) -> None:
    from eegunify.config.loader import ConfigError, load_config
    from eegunify.pipeline import convert as run

    try:
        cfg = load_config(config)
    except ConfigError as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    results = run(cfg, subjects=subject, project_root=root, **kwargs)
    colours = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
    table = Table("subject", "status", "QA", "output / error", title=cfg.dataset.name)
    for r in results:
        qa = f"[{colours[r.qa_status]}]{r.qa_status}" if r.qa_status else "-"
        if r.error:
            table.add_row(r.subject, "[red]FAIL", qa, r.error)
        else:
            status = f"[yellow]{len(r.warnings)} warn" if r.warnings else "[green]ok"
            table.add_row(r.subject, status, qa, str(r.output or r.qa_report))
    console.print(table)
    if any(r.error or r.qa_status == "FAIL" for r in results):
        raise typer.Exit(EXIT_FAILED)


@app.command()
def convert(
    config: Annotated[Path, typer.Argument(exists=True, help="Dataset YAML config.")],
    subject: Annotated[
        list[str] | None, typer.Option("--subject", "-s", help="Repeatable.")
    ] = None,
    overwrite: Annotated[bool, typer.Option(help="Replace existing BIDS output.")] = False,
    no_qa: Annotated[bool, typer.Option("--no-qa", help="Skip the QA report.")] = False,
    root: Annotated[
        Path, typer.Option(help="Project root (paths in the config are relative to it).")
    ] = Path("."),
) -> None:
    """Convert a dataset to canonical BIDS-EEG in rawdata/ and write QA reports.

    Exits with 1 if any recording fails to convert or its QA status is FAIL.
    """
    _run(config, subject, root, overwrite=overwrite, qa=not no_qa)


@app.command()
def qa(
    config: Annotated[Path, typer.Argument(exists=True, help="Dataset YAML config.")],
    subject: Annotated[
        list[str] | None, typer.Option("--subject", "-s", help="Repeatable.")
    ] = None,
    root: Annotated[
        Path, typer.Option(help="Project root (paths in the config are relative to it).")
    ] = Path("."),
) -> None:
    """Rerun QA from the source recordings without writing BIDS (e.g. after a config change)."""
    _run(config, subject, root, write_bids=False)


@app.command(name="app")
def run_app(
    root: Annotated[Path, typer.Option(help="Project root shown in the app.")] = Path("."),
    port: Annotated[int, typer.Option(help="Port to serve on.")] = 8501,
) -> None:
    """Open the interactive app (needs the `app` extra: pip install eegunify[app])."""
    import importlib.util
    import subprocess
    import sys

    if importlib.util.find_spec("streamlit") is None:
        console.print("[red]Streamlit is not installed: pip install 'eegunify[app]'")
        raise typer.Exit(EXIT_USAGE)
    script = Path(__file__).parent / "app" / "main.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(script),
        "--server.port",
        str(port),
        "--",
        "--root",
        str(root.resolve()),
    ]
    raise typer.Exit(subprocess.call(cmd))


@config_app.command("validate")
def config_validate(
    config: Annotated[Path, typer.Argument(help="Dataset YAML config.")],
) -> None:
    """Check a config against the schema; lists every error by its YAML path."""
    from eegunify.config.loader import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    console.print(f"[green]valid[/]: {cfg.dataset.name} ({len(cfg.recordings.subjects)} subjects)")


@config_app.command("init")
def config_init(
    path: Annotated[Path, typer.Argument(exists=True, help="One example recording.")],
    out: Annotated[Path | None, typer.Option(help="Write the YAML here (default: stdout).")] = None,
    root: Annotated[Path, typer.Option(help="Project root.")] = Path("."),
) -> None:
    """Draft a config from one recording: paths, subject pattern, event codes, reference."""
    from eegunify.config.draft import draft_config, to_yaml
    from eegunify.pipeline import read_recording

    try:
        rec, _ = read_recording(path)
    except Exception as err:
        console.print(f"[red]could not read {path}: {type(err).__name__}: {err}")
        raise typer.Exit(EXIT_READER) from err
    text = to_yaml(draft_config(rec, path, root))
    if out:
        if out.exists():
            console.print(f"[red]{out} exists; not overwriting")
            raise typer.Exit(EXIT_USAGE)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        console.print(f"wrote {out}: edit events.mapping, then `eegunify config validate {out}`")
    else:
        typer.echo(text)


@config_app.command("schema")
def config_schema(
    out: Annotated[Path | None, typer.Option(help="Write to a file instead of stdout.")] = None,
) -> None:
    """Print the JSON Schema of the config file format."""
    from eegunify.config.schema import Config

    text = json.dumps(Config.model_json_schema(), indent=2)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
        console.print(f"wrote {out}")
    else:
        typer.echo(text)
