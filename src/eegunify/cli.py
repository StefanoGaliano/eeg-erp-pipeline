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
erp_app = typer.Typer(
    help="ERP phases: stages, cache status, component library.", no_args_is_help=True
)
app.add_typer(erp_app, name="erp")
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


@app.command()
def export(
    config: Annotated[Path, typer.Argument(exists=True, help="Dataset YAML config.")],
    to: Annotated[str, typer.Option(help="Target format (eeglab).")] = "eeglab",
    subject: Annotated[
        list[str] | None, typer.Option("--subject", "-s", help="Repeatable.")
    ] = None,
    overwrite: Annotated[bool, typer.Option(help="Replace existing exports.")] = False,
    root: Annotated[Path, typer.Option(help="Project root.")] = Path("."),
) -> None:
    """Export converted recordings for MATLAB (derivatives/eegunify-eeglab/)."""
    from eegunify.config.loader import ConfigError, load_config, resolve_for_subject
    from eegunify.export import export_eeglab

    if to != "eeglab":
        console.print(f"[red]unsupported export format {to!r} (supported: eeglab)")
        raise typer.Exit(EXIT_USAGE)
    try:
        cfg = load_config(config)
    except ConfigError as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    failed = False
    for sub in subject or cfg.recordings.subjects:
        try:
            out = export_eeglab(resolve_for_subject(cfg, sub), sub, root.resolve(), overwrite)
            console.print(f"[green]ok[/] {out}")
        except (FileNotFoundError, FileExistsError) as err:
            console.print(f"[red]sub-{sub}: {err}")
            failed = True
    if failed:
        raise typer.Exit(EXIT_FAILED)


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


fetch_app = typer.Typer(
    help="Download public EEG data (only the files needed).", no_args_is_help=True
)
app.add_typer(fetch_app, name="fetch")


@fetch_app.command("erpcore")
def fetch_erpcore_cmd(
    component: Annotated[str, typer.Option(help="N170, P3, N2pc, MMN, N400, ERN or LRP.")] = "N170",
    subject: Annotated[
        list[str] | None, typer.Option("--subject", "-s", help="015-019; repeatable.")
    ] = None,
    dest: Annotated[Path, typer.Option(help="Download folder.")] = Path("sourcedata/erpcore"),
) -> None:
    """ERP CORE (BIDS, OSF): extracts one component for the chosen subjects from the archive."""
    from eegunify.datasets import fetch_erpcore

    try:
        paths = fetch_erpcore(dest, component, subject or ["015"])
    except ValueError as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    for p in paths:
        console.print(f"[green]ok[/] {p}")


@fetch_app.command("erpcore-values")
def fetch_erpcore_values_cmd(
    component: Annotated[
        list[str] | None,
        typer.Option("--component", "-c", help="N170, P3 or N2pc; repeatable (default: all)."),
    ] = None,
    dest: Annotated[Path, typer.Option(help="Download folder.")] = Path("sourcedata/erpcore"),
    out: Annotated[Path, typer.Option(help="Where the tidy TSVs go.")] = Path(
        "tests/golden/erpcore_reference"
    ),
) -> None:
    """ERP CORE published per-subject measurements (OSF) as tidy TSVs for the comparison."""
    from eegunify.reference_values import (
        ERPCORE_VALUES,
        fetch_erpcore_values,
        tidy_component,
        write_tidy,
    )

    for comp in component or list(ERPCORE_VALUES):
        try:
            folder = fetch_erpcore_values(dest, comp)
            values, subjects = tidy_component(folder, comp)
        except ValueError as err:
            console.print(f"[red]{err}")
            raise typer.Exit(EXIT_USAGE) from err
        write_tidy(values, subjects, out, comp)
        n_in = int(subjects["included"].sum())
        console.print(f"[green]ok[/] {comp}: {n_in}/{len(subjects)} subjects included -> {out}")


@fetch_app.command("openneuro")
def fetch_openneuro_cmd(
    dataset: Annotated[str, typer.Argument(help="Accession, e.g. ds005841.")],
    subject: Annotated[str | None, typer.Option("--subject", "-s", help="Default: first.")] = None,
    tag: Annotated[str | None, typer.Option(help="Snapshot tag (default: latest).")] = None,
    dest: Annotated[Path, typer.Option(help="Download folder.")] = Path("sourcedata"),
    max_mb: Annotated[float | None, typer.Option(help="Refuse if larger than this.")] = None,
) -> None:
    """One subject's EEG files from an OpenNeuro snapshot."""
    from eegunify.datasets import fetch_openneuro

    try:
        tag, sub, paths = fetch_openneuro(
            dataset, dest, subject, tag, int(max_mb * 1e6) if max_mb else None
        )
    except (ValueError, RuntimeError) as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    console.print(f"[green]ok[/] {dataset}@{tag} sub-{sub}: {len(paths)} files in {dest / dataset}")


@app.command("validate-datasets")
def validate_datasets(
    registry: Annotated[Path, typer.Argument(exists=True, help="validation_datasets.yaml")] = Path(
        "code/configs/validation_datasets.yaml"
    ),
    workdir: Annotated[Path, typer.Option(help="Downloads, projects, summary.")] = Path(
        "derivatives/validation"
    ),
    only: Annotated[list[str] | None, typer.Option(help="Dataset id; repeatable.")] = None,
    max_mb: Annotated[float, typer.Option(help="Skip subjects larger than this.")] = 300.0,
    dry_run: Annotated[bool, typer.Option(help="Only list subjects and sizes.")] = False,
) -> None:
    """Fetch one subject per dataset, convert with an auto-drafted config, summarise QA."""
    from eegunify.validation import load_registry, plan, validate_entry, write_summary

    entries = [e for e in load_registry(registry) if not only or e["id"] in only]
    if dry_run:
        table = Table("id", "source", "format", "subject", "MB")
        for e in entries:
            try:
                sub, mb = plan(e)
            except Exception as err:
                sub, mb = f"[red]{type(err).__name__}", None
            table.add_row(
                e["id"],
                e["source"],
                str(e.get("format", "")),
                sub,
                f"{mb:.0f}" if mb is not None else "-",
            )
        console.print(table)
        return
    workdir.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for e in entries:
        console.print(f"[bold]{e['id']}[/] ({e['source']}) ...")
        outcomes.append(validate_entry(e, workdir, max_mb))
        o = outcomes[-1]
        console.print(f"  {o.status or o.stage} {o.error[:120]}")
        write_summary(outcomes, workdir)  # incremental: survives interruption
    console.print(f"summary: {workdir / 'summary.tsv'}")


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
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Markdown reference (docs/config_reference.md).")
    ] = False,
) -> None:
    """Print the JSON Schema of the config file format (or a Markdown reference)."""
    from eegunify.config.reference import markdown as to_markdown
    from eegunify.config.schema import Config

    text = to_markdown().rstrip() if markdown else json.dumps(Config.model_json_schema(), indent=2)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
        console.print(f"wrote {out}")
    else:
        typer.echo(text)


@erp_app.command("components")
def erp_components(
    preset: Annotated[str | None, typer.Option(help="Only this preset family.")] = None,
    show: Annotated[
        str | None, typer.Option(help="Print one component (id or id@preset) in full.")
    ] = None,
) -> None:
    """List the component library: windows, sites and sources of every preset."""
    from eegunify.erp.components.library import LibraryError, load_library, split_key

    library = load_library()
    if show:
        keys = [k for k in library if k == show or split_key(k)[0] == show]
        if preset:
            keys = [k for k in keys if split_key(k)[1] == preset]
        if not keys:
            console.print(f"[red]unknown component {show!r}; known: {sorted(library)}")
            raise typer.Exit(EXIT_USAGE)
        import yaml

        for k in sorted(keys):
            typer.echo(yaml.safe_dump({k: library[k].model_dump(mode="json")}, sort_keys=False))
        return
    try:
        keys = sorted(k for k in library if preset is None or split_key(k)[1] == preset)
    except LibraryError as err:  # pragma: no cover - keys were validated on load
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    if not keys:
        console.print(f"[red]no components with preset {preset!r}")
        raise typer.Exit(EXIT_USAGE)
    table = Table("key", "name", "window (ms)", "sites", "wave", "polarity")
    for k in keys:
        c = library[k]
        table.add_row(
            k,
            c.name,
            f"{c.window_ms[0]:g} to {c.window_ms[1]:g}",
            ", ".join(c.rois.primary),
            c.contrast or "conditions",
            c.polarity,
        )
    console.print(table)
    console.print("sources and full definitions: eegunify erp components --show <id>")


@erp_app.command("run")
def erp_run(
    config: Annotated[Path, typer.Argument(help="Dataset YAML config.")],
    subject: Annotated[list[str] | None, typer.Option("--subject", "-s")] = None,
    from_: Annotated[str | None, typer.Option("--from", help="First stage to run.")] = None,
    to: Annotated[str | None, typer.Option("--to", help="Last stage to run.")] = None,
    force: Annotated[
        list[str] | None, typer.Option(help="Rerun this stage and everything after it.")
    ] = None,
    root: Annotated[Path, typer.Option(help="Project root.")] = Path("."),
) -> None:
    """Run the ERP stages; cached stages whose inputs and settings are unchanged are skipped."""
    from eegunify.config.loader import ConfigError, load_config
    from eegunify.erp.runner import Runner, RunnerRefusedError

    try:
        cfg = load_config(config)
        runner = Runner(cfg, root)
        outcomes = runner.run(subject, from_=from_, to=to, force=force or ())
    except (ConfigError, ValueError) as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    except RunnerRefusedError as err:
        console.print(f"[red]refusing to run:[/]\n{err}")
        raise typer.Exit(EXIT_USAGE) from err
    colour = {"ok": "green", "skipped": "dim", "failed": "red"}
    for o in outcomes:
        msg = f" {o.message}" if o.message else ""
        console.print(f"{o.item.stage:8} {o.item.label:6} [{colour[o.status]}]{o.status}[/]{msg}")
    if any(o.status == "failed" for o in outcomes):
        raise typer.Exit(EXIT_FAILED)


@erp_app.command("status")
def erp_status(
    config: Annotated[Path, typer.Argument(help="Dataset YAML config.")],
    subject: Annotated[list[str] | None, typer.Option("--subject", "-s")] = None,
    root: Annotated[Path, typer.Option(help="Project root.")] = Path("."),
) -> None:
    """Subject x stage table of the cache: ok / stale / missing / failed. Runs nothing."""
    from eegunify.config.loader import ConfigError, load_config
    from eegunify.erp.runner import Runner

    try:
        cfg = load_config(config)
        runner = Runner(cfg, root)
        items = runner.status(subject)
    except (ConfigError, ValueError) as err:
        console.print(f"[red]{err}")
        raise typer.Exit(EXIT_USAGE) from err
    if not runner.stages:
        console.print("no ERP stages enabled by this config")
        return
    colour = {"ok": "green", "stale": "yellow", "missing": "dim", "failed": "red"}
    table = Table("subject", *runner.names)
    rows: dict[str, dict[str, str]] = {}
    for it in items:
        rows.setdefault(it.label, {})[it.stage] = f"[{colour[it.status]}]{it.status}"
    for label, cells in rows.items():
        table.add_row(label, *(cells.get(n, "") for n in runner.names))
    console.print(table)
