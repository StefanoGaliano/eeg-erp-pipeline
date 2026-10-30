"""BioSemi BDF (24-bit EDF variant with a ``Status`` trigger channel)."""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register


@register
class BdfReader:
    name = "bdf"
    extensions = (".bdf",)

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_bdf(path, preload=True, verbose="error")
        return ReadResult(
            raw,
            [path],
            "mne.io.read_raw_bdf",
            # BioSemi is reference-free: the CMS/DRL loop is not a reference electrode.
            default_reference="CMS/DRL (reference-free)",
            manufacturer="BioSemi",
            # Status bits 0-7 carry triggers; higher bits are CMS/battery/speed-mode flags.
            status_mask=hints.bdf_status_mask,
            notes=["BioSemi data are reference-free; re-reference before analysis"],
        )
