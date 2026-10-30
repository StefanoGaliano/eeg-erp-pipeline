"""Compumedics Curry 7 (``.dap/.dat/.rs3``) and Curry 8 (``.cdt/.cdt.dpa``)."""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register

CURRY_TRIGGER_OFF = 50000


def _companions(path: Path) -> list[Path]:
    """All files belonging to the recording: same stem, any Curry extension."""
    stem = path.name.split(".")[0]
    exts = (".cdt", ".cdt.dpa", ".cdt.cef", ".dap", ".dat", ".rs3", ".cef", ".ceo", ".cdt.ceo")
    out = [path.parent / f"{stem}{e}" for e in exts]
    return [p for p in out if p.is_file()]


@register
class CurryReader:
    name = "curry"
    extensions = (".cdt", ".dap")

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_curry(path, preload=True, verbose="error")
        return ReadResult(
            raw,
            _companions(path) or [path],
            "mne.io.read_raw_curry",
            manufacturer="Compumedics Neuroscan",
            # Curry writes code 50000 2 ms after every trigger (trigger-off marker); without it
            # the events equal the BDF original (mne-testing-data test_bdf_stim_channel).
            ignore_codes=frozenset({CURRY_TRIGGER_OFF}),
            notes=[f"Curry code {CURRY_TRIGGER_OFF} (trigger off) ignored"],
        )
