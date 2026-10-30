"""Write the per-recording QA report (HTML + JSON) and the dataset-level summary.tsv."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

import matplotlib.pyplot as plt
import mne
import pandas as pd

from eegunify.canonical import Recording
from eegunify.qa import plots
from eegunify.qa.checks import QAResult

_COLOURS = {"pass": "#2e7d32", "warn": "#ef6c00", "fail": "#c62828"}


def _checks_html(result: QAResult) -> str:
    rows = "".join(
        f"<tr><td><code>{c.id}</code></td>"
        f"<td style='color:{_COLOURS[c.level]};font-weight:bold'>{c.level.upper()}</td>"
        f"<td>{escape(c.message)}</td></tr>"
        for c in result.checks
    )
    status = result.status
    return (
        f"<h3 style='color:{_COLOURS[status]}'>Overall: {status.upper()}</h3>"
        f"<table class='table table-sm'><tr><th>check</th><th>level</th><th>detail</th></tr>"
        f"{rows}</table>"
    )


def _header_html(rec: Recording, result: QAResult) -> str:
    prov = rec.provenance
    files = "".join(
        f"<li><code>{escape(Path(f['path']).name)}</code> "
        f"sha256 <code>{f['sha256'][:16]}…</code></li>"
        for f in prov.get("source_files", [])
    )
    versions = ", ".join(f"{k} {v}" for k, v in prov.get("versions", {}).items())
    ex = result.extra
    return (
        f"<p><b>Format:</b> {rec.format} &nbsp; <b>sfreq:</b> {ex['sfreq']:g} Hz &nbsp; "
        f"<b>duration:</b> {ex['duration_s']:.1f} s &nbsp; <b>reference:</b> "
        f"{escape(rec.reference)} &nbsp; <b>channels:</b> {ex['n_channels']}</p>"
        f"<ul>{files}</ul><p><small>{escape(versions)}</small></p>"
    )


def _events_html(rec: Recording, result: QAResult) -> str:
    table = rec.events.table
    if table.empty:
        return "<p>No events.</p>"
    g = table.groupby("trial_type")["onset"].agg(["count", "min", "max"]).reset_index()
    g.columns = ["trial_type", "n", "first onset (s)", "last onset (s)"]
    unmapped = rec.events.unmapped_codes
    note = f"<p>Unmapped codes: {escape(str(unmapped))}</p>" if unmapped else ""
    html = g.to_html(index=False, float_format="{:.2f}".format, classes=["table", "table-sm"])
    return html + note


def write_report(rec: Recording, result: QAResult, out_dir: Path, basename: str) -> Path:
    """Write ``<basename>_qa.html`` and ``<basename>_qa.json`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = mne.Report(title=f"eegunify QA: {basename}", verbose="error")
    report.add_html(_checks_html(result), title="Checks", section="Summary")
    report.add_html(_header_html(rec, result), title="Recording", section="Summary")
    report.add_html(
        result.channels.to_html(index=False, classes=["table", "table-sm"]),
        title="Channels",
        section="Channels",
    )
    report.add_html(_events_html(rec, result), title="Events", section="Events")
    figures = [
        (plots.psd_figure(rec.raw, result.line_freq), "Power spectrum", "Signal"),
        (plots.roi_trace_figure(rec.raw), "Visual ROI traces", "Signal"),
        (plots.sensor_figure(rec.raw, rec.missing_positions), "Sensor layout", "Channels"),
        (plots.event_figure(rec.events.table), "Event raster", "Events"),
    ]
    for fig, title, section in figures:
        report.add_figure(fig, title=title, section=section, image_format="png")
        plt.close(fig)
    html = out_dir / f"{basename}_qa.html"
    report.save(html, open_browser=False, overwrite=True, verbose="error")
    (out_dir / f"{basename}_qa.json").write_text(
        json.dumps(result.to_json(), indent=2, default=str) + "\n", encoding="utf-8"
    )
    return html


SUMMARY_COLUMNS = [
    "recording",
    "subject",
    "format",
    "status",
    "n_fail",
    "n_warn",
    "failed_checks",
    "warned_checks",
    "report",
]


def update_summary(
    summary: Path, rec: Recording, result: QAResult, basename: str, report: Path
) -> None:
    """Insert or replace this recording's row in the dataset-level ``summary.tsv``."""
    row = {
        "recording": basename,
        "subject": rec.subject,
        "format": rec.format,
        "status": result.status.upper(),
        "n_fail": sum(c.level == "fail" for c in result.checks),
        "n_warn": sum(c.level == "warn" for c in result.checks),
        "failed_checks": ",".join(c.id for c in result.checks if c.level == "fail"),
        "warned_checks": ",".join(c.id for c in result.checks if c.level == "warn"),
        "report": report.relative_to(summary.parent).as_posix(),
    }
    if summary.exists():
        df_old = pd.read_csv(summary, sep="\t", dtype=str, keep_default_na=False)
        df_old = df_old[df_old["recording"] != basename]
        table = pd.concat([df_old, pd.DataFrame([row])], ignore_index=True)
    else:
        table = pd.DataFrame([row], columns=SUMMARY_COLUMNS)
    table.sort_values("recording").to_csv(summary, sep="\t", index=False)
