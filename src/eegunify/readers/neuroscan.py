"""Neuroscan ``.cnt`` (Scan 4.x).

The format does not say reliably whether samples are 16- or 32-bit, and some headers record 0
samples, so MNE cannot guess from the file size either (mne-testing-data ``scan41_short.cnt``).
With ``format.hints.neuroscan_data_format: auto`` both widths are read and the one with
physiological amplitudes wins: the wrong width gives absurd values (2.6 V instead of 40 µV for
that file). If neither or both are plausible, the user must set the hint.
"""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register
from eegunify.units import robust_sd_uV

PLAUSIBLE_UV = (0.5, 500.0)


def _read(path: Path, data_format: str) -> mne.io.BaseRaw:
    return mne.io.read_raw_cnt(path, data_format=data_format, preload=True, verbose="error")


def _disambiguate(path: Path) -> tuple[mne.io.BaseRaw, str]:
    candidates = {}
    for fmt in ("int16", "int32"):
        try:
            raw = _read(path, fmt)
        except Exception:  # a width that cannot even be parsed is simply not a candidate
            continue
        sd = robust_sd_uV(raw)
        candidates[fmt] = (raw, sd)
    plausible = {f: c for f, c in candidates.items() if PLAUSIBLE_UV[0] <= c[1] <= PLAUSIBLE_UV[1]}
    if len(plausible) == 1:
        fmt, (raw, sd) = next(iter(plausible.items()))
        return raw, f"Neuroscan sample width auto-detected as {fmt} (robust SD {sd:.3g} µV)"
    sds = {f: f"{c[1]:.3g} µV" for f, c in candidates.items()}
    raise ValueError(
        f"{path.name}: cannot tell 16- from 32-bit samples (robust SD per width: {sds}); "
        "set format.hints.neuroscan_data_format to int16 or int32"
    )


@register
class NeuroscanReader:
    name = "neuroscan"
    extensions = (".cnt",)

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        fmt = hints.neuroscan_data_format
        if fmt == "auto":
            raw, note = _disambiguate(path)
        else:
            raw, note = _read(path, fmt), f"Neuroscan sample width {fmt} (from config)"
        return ReadResult(
            raw, [path], "mne.io.read_raw_cnt", manufacturer="Neuroscan", notes=[note]
        )
