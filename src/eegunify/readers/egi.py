"""EGI / Magstim Net Station: ``.mff`` (a folder) or legacy simple binary ``.raw``.

Channels are named ``E1..E256`` (HydroCel layout) and keep those names: there is no 10-20
equivalent, so the montage defaults to the GSN-HydroCel layout of the matching size.
"""

from __future__ import annotations

from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register


@register
class EgiReader:
    name = "egi"
    extensions = (".mff", ".raw")

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        raw = mne.io.read_raw_egi(path, preload=True, events_as_annotations=True, verbose="error")
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        return ReadResult(
            raw,
            [path] if path.is_dir() else files,
            "mne.io.read_raw_egi",
            manufacturer="EGI (Magstim)",
            # One binary stim channel per event type (DIN1, DIN2, ...): reading only the first
            # would lose every other type. The annotations hold all of them.
            event_source="annotations",
        )
