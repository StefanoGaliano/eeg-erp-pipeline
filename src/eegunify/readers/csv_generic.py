"""Delimited text recordings: OpenBCI GUI exports and generic CSV/TSV.

Text formats carry no reliable metadata, so this reader is explicit about every guess: the
sampling rate comes from the config, an OpenBCI ``%Sample Rate`` header line, or the median
spacing of a time column (in that order), and each guess is recorded as a note.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import mne
import numpy as np
import pandas as pd

from eegunify.config.schema import CsvHints, FormatHints
from eegunify.readers.base import ReadResult, register
from eegunify.units import TO_VOLTS

_RATE_RE = re.compile(r"sample\s*rate\s*[=:]\s*([\d.]+)", re.IGNORECASE)
_OPENBCI_EXG = re.compile(r"^EXG Channel (\d+)$", re.IGNORECASE)
_TIME_RE = re.compile(r"^(time|timestamp|time_s|seconds|t)$", re.IGNORECASE)
_MARKER_RE = re.compile(r"(marker|trigger|event|stim)", re.IGNORECASE)
_INDEX_RE = re.compile(r"^(sample index|sample|index|sample_index)$", re.IGNORECASE)


def _header_rate(path: Path, comment: str) -> float | None:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith(comment):
                break
            m = _RATE_RE.search(line)
            if m:
                return float(m.group(1))
    return None


def _sniff_delimiter(path: Path, comment: str) -> str:
    with path.open(encoding="utf-8", errors="replace") as fh:
        lines = [ln for ln in fh if not ln.startswith(comment)][:5]
    try:
        return csv.Sniffer().sniff("".join(lines), delimiters=",;\t ").delimiter
    except csv.Error:
        return ","


def _pick(columns: list[str], explicit: str | None, pattern: re.Pattern[str]) -> str | None:
    if explicit:
        if explicit not in columns:
            raise ValueError(f"column {explicit!r} not found; columns: {columns}")
        return explicit
    return next((c for c in columns if pattern.search(c)), None)


def read_csv_recording(path: Path, hints: CsvHints) -> tuple[mne.io.RawArray, list[str]]:
    notes: list[str] = []
    delimiter = hints.delimiter or _sniff_delimiter(path, hints.comment)
    table = pd.read_csv(path, sep=delimiter, comment=hints.comment, skipinitialspace=True)
    table.columns = [str(c).strip() for c in table.columns]
    columns = list(table.columns)

    time_col = _pick(columns, hints.time_column, _TIME_RE)
    marker_col = _pick(columns, hints.marker_column, _MARKER_RE)
    if hints.channel_columns:
        missing = [c for c in hints.channel_columns if c not in columns]
        if missing:
            raise ValueError(f"csv.channel_columns not in file: {missing}; columns: {columns}")
        data_cols = list(hints.channel_columns)
    else:
        exg = [c for c in columns if _OPENBCI_EXG.match(c)]
        excluded = {time_col, marker_col}
        data_cols = exg or [
            c
            for c in columns
            if c not in excluded
            and not _INDEX_RE.match(c)
            and pd.api.types.is_numeric_dtype(table[c])
        ]
        if exg:
            notes.append(f"OpenBCI file: {len(exg)} EXG channels")
    if not data_cols:
        raise ValueError(f"no data columns found; columns: {columns}")

    sfreq = hints.sfreq
    if sfreq:
        notes.append(f"sampling rate {sfreq:g} Hz (from config)")
    elif (rate := _header_rate(path, hints.comment)) is not None:
        sfreq = rate
        notes.append(f"sampling rate {sfreq:g} Hz (from file header)")
    elif time_col is not None:
        dt = float(np.median(np.diff(table[time_col].to_numpy(float))))
        if dt <= 0:
            raise ValueError(f"time column {time_col!r} is not increasing")
        sfreq = round(1.0 / dt, 6)
        notes.append(f"sampling rate {sfreq:g} Hz (from the median step of {time_col!r})")
    else:
        raise ValueError("unknown sampling rate: set format.hints.csv.sfreq")

    names = hints.channel_names or [
        f"EXG{int(m.group(1)) + 1}" if (m := _OPENBCI_EXG.match(c)) else c for c in data_cols
    ]
    data = table[data_cols].to_numpy(float).T * TO_VOLTS[hints.unit]
    raw = mne.io.RawArray(data, mne.create_info(names, sfreq, "eeg"), verbose="error")
    if marker_col is not None:
        codes = table[marker_col].to_numpy()
        hits = np.flatnonzero(pd.notna(codes) & (codes != 0) & (codes != "0"))
        if hits.size:
            labels = [
                str(int(c))
                if isinstance(c, (int, float, np.number)) and float(c).is_integer()
                else str(c)
                for c in codes[hits]
            ]
            raw.set_annotations(mne.Annotations(hits / sfreq, np.zeros(hits.size), labels))
        notes.append(f"{hits.size} markers from column {marker_col!r}")
    return raw, notes


@register
class CsvReader:
    name = "csv"
    extensions = (".csv", ".tsv", ".txt")

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw, notes = read_csv_recording(path, hints.csv)
        return ReadResult(raw, [path], "eegunify.readers.csv_generic", notes=notes)
