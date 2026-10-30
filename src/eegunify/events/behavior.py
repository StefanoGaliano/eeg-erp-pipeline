"""Behavioural logs (PsychoPy, Presentation, E-Prime) aligned to EEG events.

Triggers get lost and the stimulus PC's clock differs from the amplifier's, so logs are not
paired with EEG events by row number when they can be paired by time. With a stimulus time in
the log, the clock offset is estimated from every candidate pair (the offset most pairs agree
on), then each EEG event takes the nearest unused log row within ``max_drift_ms`` after a
linear fit (offset + drift). Dropped triggers and extra log rows then stay unmatched instead of
shifting every later trial onto the wrong row.
"""

from __future__ import annotations

import fnmatch
import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from eegunify.config.schema import BehaviorSection

_PSYCHOPY_TIME = re.compile(r"\.started$")


@dataclass
class AlignmentReport:
    source: str
    file: str
    n_eeg: int
    """EEG events selected for alignment."""
    n_beh: int
    """Log rows selected for alignment."""
    matched: int
    method: str
    """``time`` or ``order``."""
    offset_s: float | None = None
    drift_ppm: float | None = None
    max_residual_ms: float | None = None
    unmatched_eeg: int = 0
    unmatched_beh: int = 0


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # E-Prime exports are often UTF-16
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def read_psychopy(path: Path) -> pd.DataFrame:
    table = pd.read_csv(path)
    return table.dropna(how="all").reset_index(drop=True)


def read_presentation(path: Path) -> pd.DataFrame:
    """Presentation ``.log``: header lines, then the event table up to the first blank line.

    ``Time`` is in units of 0.1 ms; a ``time_s`` column in seconds is added.
    """
    lines = _read_text(path).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Subject\t"))
    body = []
    for ln in lines[start:]:
        if not ln.strip():
            break
        body.append(ln)
    table = pd.read_csv(io.StringIO("\n".join(body)), sep="\t", dtype=str)
    table["time_s"] = pd.to_numeric(table["Time"], errors="coerce") / 10_000
    return table


def read_eprime(path: Path) -> pd.DataFrame:
    """E-DataAid tab-delimited export (first line may be the file name)."""
    lines = _read_text(path).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.count("\t") >= 2)
    return pd.read_csv(io.StringIO("\n".join(lines[start:])), sep="\t")


READERS = {"psychopy": read_psychopy, "presentation": read_presentation, "eprime": read_eprime}


def find_log(cfg: BehaviorSection, source_dir: Path, subject: str) -> Path:
    assert cfg.file is not None
    pattern = cfg.file.format(subject=subject)
    hits = sorted(source_dir.glob(pattern))
    if len(hits) != 1:
        raise ValueError(f"behavior.file {pattern!r} matched {len(hits)} files in {source_dir}")
    return hits[0]


def _time_column(cfg: BehaviorSection, beh: pd.DataFrame) -> str | None:
    if cfg.align_on.beh_time_column:
        if cfg.align_on.beh_time_column not in beh.columns:
            raise ValueError(f"behavior time column {cfg.align_on.beh_time_column!r} not in log")
        return cfg.align_on.beh_time_column
    if "time_s" in beh.columns:
        return "time_s"
    started = [
        c
        for c in beh.columns
        if _PSYCHOPY_TIME.search(str(c))
        and pd.api.types.is_numeric_dtype(beh[c])
        and beh[c].notna().any()
    ]
    return started[0] if started else None


def _match_by_time(
    eeg_t: np.ndarray, beh_t: np.ndarray, tol: float
) -> tuple[dict[int, int], float, float]:
    """EEG index -> log index. Returns the mapping, offset (s) and drift (ppm)."""
    diffs = (eeg_t[:, None] - beh_t[None, :]).ravel()
    # The offset most candidate pairs agree on (within tol) is the clock offset.
    diffs.sort()
    counts = np.searchsorted(diffs, diffs + tol) - np.arange(diffs.size)
    best = int(np.argmax(counts))
    offset = float(np.median(diffs[best : best + counts[best]]))
    slope, intercept = 1.0, offset
    pairs: dict[int, int] = {}
    for _ in range(2):  # match, refit offset + drift on the matches, match again
        pairs = {}
        used: set[int] = set()
        predicted = slope * beh_t + intercept
        for i, t in enumerate(eeg_t):
            order = np.argsort(np.abs(predicted - t))
            for j in order[:3]:
                if abs(predicted[j] - t) <= tol and j not in used:
                    pairs[i] = int(j)
                    used.add(int(j))
                    break
        if len(pairs) >= 3:
            ei = np.fromiter(pairs.keys(), int)
            bj = np.fromiter(pairs.values(), int)
            slope, intercept = np.polyfit(beh_t[bj], eeg_t[ei], 1)
    return pairs, float(intercept), float((slope - 1.0) * 1e6)


def align(
    events: pd.DataFrame, beh: pd.DataFrame, cfg: BehaviorSection, source: str, file: str
) -> tuple[pd.DataFrame, AlignmentReport]:
    """Append ``beh_<column>`` columns to the matched rows of the EEG event table."""
    selected = (
        events["trial_type"]
        .astype(str)
        .map(lambda tt: any(fnmatch.fnmatch(tt, p) for p in cfg.align_on.eeg_trial_types))
    )
    eeg_idx = np.flatnonzero(selected.to_numpy())
    rows = beh.query(cfg.align_on.beh_filter) if cfg.align_on.beh_filter else beh
    time_col = _time_column(cfg, rows)
    if time_col is not None:
        rows = rows[rows[time_col].notna()]
    rows = rows.reset_index(drop=True)
    missing = [c for c in cfg.columns if c not in rows.columns]
    if missing:
        raise ValueError(f"behavior.columns not in the log: {missing}")

    report = AlignmentReport(source, file, len(eeg_idx), len(rows), 0, "order")
    if time_col is not None and len(eeg_idx) and len(rows):
        eeg_t = events["onset"].to_numpy(float)[eeg_idx]
        beh_t = rows[time_col].to_numpy(float)
        pairs, offset, drift = _match_by_time(eeg_t, beh_t, cfg.max_drift_ms / 1000)
        report.method, report.offset_s, report.drift_ppm = "time", offset, drift
        if pairs:
            ei = np.fromiter(pairs.keys(), int)
            bj = np.fromiter(pairs.values(), int)
            fit = (
                np.polyval(np.polyfit(beh_t[bj], eeg_t[ei], 1), beh_t[bj])
                if len(ei) >= 3
                else beh_t[bj] + offset
            )
            report.max_residual_ms = float(np.abs(eeg_t[ei] - fit).max() * 1000)
    else:
        if len(eeg_idx) != len(rows):
            raise ValueError(
                f"cannot pair {len(eeg_idx)} EEG events with {len(rows)} log rows by order; "
                "set behavior.align_on.beh_time_column or beh_filter"
            )
        pairs = dict(enumerate(range(len(rows))))

    out = events.copy()
    for col in cfg.columns:
        values: list[object] = [pd.NA] * len(out)
        source_values = rows[col].tolist()
        for i, j in pairs.items():
            values[int(eeg_idx[i])] = source_values[j]
        out[f"beh_{col}"] = pd.Series(values, index=out.index, dtype=object)
    report.matched = len(pairs)
    report.unmatched_eeg = len(eeg_idx) - len(pairs)
    report.unmatched_beh = len(rows) - len(pairs)
    return out, report


def attach_behavior(
    events: pd.DataFrame, cfg: BehaviorSection, source_dir: Path, subject: str
) -> tuple[pd.DataFrame, AlignmentReport]:
    path = find_log(cfg, source_dir, subject)
    beh = READERS[cfg.source](path)
    return align(events, beh, cfg, cfg.source, path.name)
