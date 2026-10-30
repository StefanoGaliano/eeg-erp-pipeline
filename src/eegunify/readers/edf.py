"""EDF / EDF+ (16-bit)."""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register


@register
class EdfReader:
    name = "edf"
    extensions = (".edf",)

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_edf(path, preload=True, verbose="error")
        return ReadResult(raw, [path], "mne.io.read_raw_edf")
