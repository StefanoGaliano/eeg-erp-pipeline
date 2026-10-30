"""GDF 1.x / 2.x (g.tec, BioSig; BCI Competition data)."""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register


@register
class GdfReader:
    name = "gdf"
    extensions = (".gdf",)

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_gdf(path, preload=True, verbose="error")
        return ReadResult(raw, [path], "mne.io.read_raw_gdf")
