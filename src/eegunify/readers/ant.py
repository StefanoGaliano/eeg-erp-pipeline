"""ANT Neuro eego (``.cnt`` RIFF container, events in a sibling ``.evt``)."""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register, sidecars


@register
class AntReader:
    name = "ant"
    extensions = (".cnt",)  # shared with Neuroscan; detect.py tells them apart by magic bytes

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_ant(path, preload=True, verbose="error")
        return ReadResult(
            raw, sidecars(path, ".evt", ".seg"), "mne.io.read_raw_ant", manufacturer="ANT Neuro"
        )
