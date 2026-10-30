"""Real-world validation harness (blueprint step 14b).

For every entry of ``code/configs/validation_datasets.yaml``: fetch one subject, draft a config
automatically (``config.draft``), convert it with QA, and record the outcome. Nothing is tuned
per dataset: this measures how eegunify does on data it has never seen. The result is
``<workdir>/summary.tsv`` and a Markdown table (``summary.md``) for the README support matrix.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from eegunify import datasets
from eegunify.config.draft import draft_config, draft_paths
from eegunify.config.loader import parse_config
from eegunify.detect import EXTENSIONS, UnknownFormatError, detect_format
from eegunify.pipeline import convert, read_recording

log = logging.getLogger(__name__)

AUTOMATED_SOURCES = ("openneuro", "erpcore")


@dataclass
class Outcome:
    id: str
    source: str
    expected_format: str
    stage: str = "pending"  # skipped | fetch | read | convert | done
    status: str = ""  # PASS / WARN / FAIL / ERROR / SKIP
    detected_format: str = ""
    subject: str = ""
    file: str = ""
    mb: float = 0.0
    n_eeg: int = 0
    sfreq: float = 0.0
    n_events: int = 0
    failed_checks: str = ""
    warned_checks: str = ""
    error: str = ""
    seconds: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def load_registry(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["datasets"])


def _entry_files(files: list[Path]) -> list[Path]:
    """Recordings that eegunify can open directly (companions such as .eeg/.fdt excluded)."""
    out = []
    for f in sorted(files):
        try:
            if f.suffix.lower() in EXTENSIONS or f.suffix.lower() in (".cnt", ".raw"):
                detect_format(f)
                out.append(f)
        except UnknownFormatError:
            continue
    return out


def plan(entry: dict[str, Any]) -> tuple[str, float | None]:
    """Without downloading: the subject and its size in MB (OpenNeuro only)."""
    if entry["source"] != "openneuro":
        return "", None
    _, subject, files = datasets.openneuro_subject_files(entry["id"], first_recording_only=True)
    return subject, sum(size for _, size, _ in files) / 1e6


def _fetch(entry: dict[str, Any], data_dir: Path, max_mb: float) -> tuple[str, list[Path]]:
    if entry["source"] == "openneuro":
        _, subject, files = datasets.fetch_openneuro(
            entry["id"], data_dir, max_bytes=int(max_mb * 1e6), first_recording_only=True
        )
        return subject, files
    if entry["source"] == "erpcore":
        paths = datasets.fetch_erpcore(data_dir / "erpcore", "N170", ["015"])
        return "015", paths
    raise NotImplementedError(entry["source"])


def validate_entry(entry: dict[str, Any], workdir: Path, max_mb: float) -> Outcome:
    out = Outcome(entry["id"], entry["source"], str(entry.get("format", "")))
    start = time.monotonic()
    try:
        if entry["source"] not in AUTOMATED_SOURCES:
            out.stage, out.status = "skipped", "SKIP"
            out.error = f"source {entry['source']!r} not automated yet"
            return out
        out.stage = "fetch"
        out.subject, files = _fetch(entry, workdir / "data", max_mb)
        out.mb = round(sum(f.stat().st_size for f in files if f.is_file()) / 1e6, 1)
        candidates = _entry_files(files)
        if not candidates:
            raise RuntimeError(f"no readable EEG file among {[f.name for f in files][:6]}")
        recording = candidates[0]
        out.file = recording.name
        out.detected_format = detect_format(recording)

        # A throwaway project per dataset, with the data linked in as sourcedata/.
        project = workdir / "projects" / entry["id"]
        source_dir = project / "sourcedata"
        source_dir.mkdir(parents=True, exist_ok=True)
        link = source_dir / "data"
        if not link.exists():
            link.symlink_to((workdir / "data").resolve(), target_is_directory=True)
        rel_file = link / recording.resolve().relative_to((workdir / "data").resolve())

        out.stage = "read"
        try:
            rec, _ = read_recording(rel_file)
            cfg_dict = draft_config(rec, rel_file, project)
        except Exception:
            cfg_dict = draft_paths(rel_file, project)  # convert reports the real error
        cfg_dict["dataset"]["name"] = entry["id"]
        cfg = parse_config(cfg_dict)

        out.stage = "convert"
        [res] = convert(cfg, project_root=project, overwrite=True)
        if res.error:
            raise RuntimeError(res.error)
        out.stage, out.status = "done", res.qa_status or ""
        rec, _ = read_recording(rel_file, cfg)
        out.n_eeg = len(rec.raw.copy().pick("eeg").ch_names)
        out.sfreq = float(rec.raw.info["sfreq"])
        out.n_events = len(rec.events.table)
        summary = pd.read_csv(
            project / "derivatives" / "eegunify-qa" / "summary.tsv",
            sep="\t",
            dtype=str,
            keep_default_na=False,
        )
        row = summary.iloc[0]
        out.failed_checks, out.warned_checks = row["failed_checks"], row["warned_checks"]
    except datasets.TooLargeError as err:
        out.stage, out.status, out.error = "skipped", "SKIP", str(err)
    except Exception as err:  # every failure is a result, not a crash
        out.status = "ERROR"
        out.error = f"{type(err).__name__}: {err}"[:300]
        log.error("%s failed at %s: %s", entry["id"], out.stage, out.error)
    finally:
        out.seconds = round(time.monotonic() - start, 1)
    return out


def write_summary(outcomes: list[Outcome], workdir: Path) -> Path:
    rows = [{k: v for k, v in asdict(o).items() if k != "extra"} for o in outcomes]
    table = pd.DataFrame(rows)
    tsv = workdir / "summary.tsv"
    previous = (
        pd.read_csv(tsv, sep="\t", dtype=str, keep_default_na=False) if tsv.exists() else None
    )
    if previous is not None:
        table = pd.concat([previous[~previous["id"].isin(table["id"])], table.astype(str)])
    table = table.sort_values("id")
    table.to_csv(tsv, sep="\t", index=False)
    md_cols = ["id", "detected_format", "status", "n_eeg", "sfreq", "n_events", "error"]
    lines = ["| " + " | ".join(md_cols) + " |", "|" + "---|" * len(md_cols)]
    for _, r in table.iterrows():
        cells = [str(r[c]).replace("|", "/")[:80] for c in md_cols]
        lines.append("| " + " | ".join(cells) + " |")
    (workdir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tsv
