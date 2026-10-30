"""Draft a dataset config from one example recording.

Used by ``eegunify config init`` and by the app's Configure page: the user starts from what the
file actually contains (event codes, channel names) instead of an empty YAML.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from eegunify.canonical import Recording

_SUB_RE = re.compile(r"sub-([A-Za-z0-9]+)")
_TASK_RE = re.compile(r"task-([A-Za-z0-9]+)")


def subject_pattern(rel_path: str) -> tuple[str, str | None]:
    """Turn one file path into a ``{subject}`` pattern.

    ``sub-001/eeg/sub-001_task-x_eeg.bdf`` -> (``sub-{subject}/eeg/sub-{subject}_task-x_eeg.bdf``,
    ``"001"``). Without a ``sub-`` entity the path is returned unchanged with subject ``None``.
    """
    m = _SUB_RE.search(rel_path)
    if not m:
        return rel_path, None
    label = m.group(1)
    return rel_path.replace(f"sub-{label}", "sub-{subject}"), label


def event_code_table(rec: Recording) -> pd.DataFrame:
    """One row per distinct original event code: ``code``, ``n``, and its current ``trial_type``."""
    table = rec.events.table
    if table.empty:
        return pd.DataFrame(columns=["code", "n", "trial_type"])
    grouped = (
        table.assign(code=table["value"].astype(str))
        .groupby("code", sort=False)
        .agg(n=("onset", "size"), trial_type=("trial_type", "first"))
        .reset_index()
    )

    def sort_key(code: str) -> tuple[int, int | str]:
        return (0, int(code)) if code.lstrip("-").isdigit() else (1, code)

    order = sorted(grouped["code"], key=sort_key)
    return grouped.set_index("code").loc[order].reset_index()


def mapping_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Build ``events.mapping`` from ``{code, trial_type}`` rows.

    Rows with an empty trial type are left out (they fall under ``events.unmapped``). Runs of
    consecutive integer codes with the same trial type are merged into ranges (``"1-40"``).
    """
    ints: list[tuple[int, str]] = []
    labels: dict[str, str] = {}
    for row in rows:
        code, trial_type = str(row["code"]).strip(), str(row.get("trial_type") or "").strip()
        if not trial_type or trial_type == "nan":
            continue
        if code.isdigit():
            ints.append((int(code), trial_type))
        else:
            labels[code] = trial_type
    ints.sort()
    out: dict[str, str] = {}
    i = 0
    while i < len(ints):
        start, tt = ints[i]
        end = start
        while i + 1 < len(ints) and ints[i + 1] == (end + 1, tt):
            i += 1
            end = ints[i][0]
        out[str(start) if start == end else f"{start}-{end}"] = tt
        i += 1
    return {**out, **labels}


def draft_paths(source_file: Path, project_root: Path) -> dict[str, Any]:
    """The ``dataset`` and ``recordings`` sections implied by one file's location.

    Works without reading the file, so it also serves recordings that fail to load.
    """
    # abspath, not resolve(): a dataset symlinked into sourcedata/ must stay inside the project.
    project_root = Path(os.path.abspath(project_root))
    source_file = Path(os.path.abspath(source_file))
    try:
        rel = source_file.relative_to(project_root)
    except ValueError:
        rel = Path(source_file.name)
    # The dataset root is the folder above the first sub-* folder (or the file's folder).
    parts = rel.parts
    sub_idx = next((i for i, p in enumerate(parts) if _SUB_RE.fullmatch(p)), len(parts) - 1)
    source_root = Path(*parts[:sub_idx]) if sub_idx else Path(".")
    pattern, subject = subject_pattern(Path(*parts[sub_idx:]).as_posix())
    if subject is None:
        pattern, subject = "{subject}" + Path(pattern).suffix, Path(pattern).stem
        subject = re.sub(r"[^A-Za-z0-9]", "", subject) or "01"
    task_m = _TASK_RE.search(source_file.name)
    return {
        "dataset": {
            "name": source_root.name or source_file.stem,
            "source_root": source_root.as_posix(),
            "task": task_m.group(1) if task_m else "task",
        },
        "recordings": {"pattern": pattern, "subjects": [subject]},
    }


def draft_config(rec: Recording, source_file: Path, project_root: Path) -> dict[str, Any]:
    """A valid starting config for the dataset that ``source_file`` belongs to."""
    codes = event_code_table(rec)
    cfg: dict[str, Any] = {
        **draft_paths(source_file, project_root),
        "reference": {"online": rec.reference},
        "events": {
            "mapping": mapping_from_rows(
                {"code": c, "trial_type": f"event/{c}"} for c in codes["code"]
            ),
            "unmapped": "keep_as_code",
        },
    }
    if rec.format == "bdf":
        cfg["montage"] = (
            {"standard_name": "biosemi64"}
            if len(rec.raw.copy().pick("eeg").ch_names) == 64
            else {"source": "standard"}
        )
    return cfg


def to_yaml(cfg: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(cfg), sort_keys=False, allow_unicode=True)
