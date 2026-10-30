"""EEGLAB ``.set`` (with optional ``.fdt`` data file)."""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register, sidecars


@register
class EeglabReader:
    name = "eeglab"
    extensions = (".set",)

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_eeglab(path, preload=True, verbose="error")
        return ReadResult(raw, sidecars(path, ".fdt"), "mne.io.read_raw_eeglab")
