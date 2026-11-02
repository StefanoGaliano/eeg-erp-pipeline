"""EGI / Magstim Net Station: ``.mff`` (a folder) or legacy simple binary ``.raw``.

Channels are named ``E1..E256`` (HydroCel layout) and keep those names: there is no 10-20
equivalent, so the montage defaults to the GSN-HydroCel layout of the matching size.

Simple binary ``.raw`` comes in a continuous (versions 2/4/6) and a segmented (3/5/7) variant.
MNE reads only the continuous one. Net Station also exports continuous recordings as
"segmented" files holding a single segment (seen in the GABA infant VEP files, Zenodo
5172962); such a file is rewritten as its continuous equivalent in a temporary folder and read
from there. Files with several segments are epoched data and are refused.
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register

#: Header fields up to and including ``value_range`` (version, date/time, rate, channels...).
_FIXED_HEADER = 30
_SEGMENT_HEADER = 6  # category index (int16) + segment start time (int32 ms)
_BYTES = {2: 2, 4: 4, 6: 8}  # precision bits of the version -> bytes per value


class EgiSegmentedError(ValueError):
    """A segmented simple binary file that is not a single continuous segment."""


def unsegment(path: Path, out_dir: Path) -> tuple[Path, str] | None:
    """Rewrite a one-segment EGI ``.raw`` as a continuous file in ``out_dir``.

    Returns ``(new_path, note)``, or ``None`` when the file is already continuous.

    Raises
    ------
    EgiSegmentedError
        If the file holds more (or fewer) than one segment, or its size does not match.
    """
    data = path.read_bytes()
    (version,) = struct.unpack(">i", data[:4])
    if version % 2 == 0:
        return None
    precision = version & 6
    if precision not in _BYTES:
        raise EgiSegmentedError(f"{path.name}: unknown EGI simple binary version {version}")
    n_channels = struct.unpack(">h", data[22:24])[0]
    off = _FIXED_HEADER
    (n_categories,) = struct.unpack(">h", data[off : off + 2])
    off += 2
    for _ in range(n_categories):
        off += 1 + data[off]  # Pascal string: length byte + name
    n_segments, n_samples, n_events = struct.unpack(">hih", data[off : off + 8])
    off += 8
    codes = data[off : off + 4 * n_events]
    off += 4 * n_events
    if n_segments != 1:
        raise EgiSegmentedError(
            f"{path.name}: segmented EGI file with {n_segments} segments (epoched data); only "
            "continuous recordings, or a single segment, can be read"
        )
    seg_start = struct.unpack(">i", data[off + 2 : off + _SEGMENT_HEADER])[0]
    body = data[off + _SEGMENT_HEADER :]
    expected = n_samples * (n_channels + n_events) * _BYTES[precision]
    if len(body) != expected:
        raise EgiSegmentedError(
            f"{path.name}: {len(body)} data bytes, expected {expected} for one segment"
        )
    header = (
        struct.pack(">i", version - 1)
        + data[4:_FIXED_HEADER]
        + struct.pack(">ih", n_samples, n_events)
        + codes
    )
    out = out_dir / path.name
    out.write_bytes(header + body)
    note = f"segmented EGI file with one segment (starting at {seg_start} ms) read as continuous"
    return out, note


@register
class EgiReader:
    name = "egi"
    extensions = (".mff", ".raw")

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        notes: list[str] = []
        if path.is_file() and path.suffix.lower() == ".raw":
            with tempfile.TemporaryDirectory(prefix="eegunify-egi-") as tmp:
                fixed = unsegment(path, Path(tmp))
                if fixed is not None:
                    notes.append(fixed[1])
                raw = self._read(fixed[0] if fixed else path)
        else:
            raw = self._read(path)
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        return ReadResult(
            raw,
            [path] if path.is_dir() else files,
            "mne.io.read_raw_egi",
            manufacturer="EGI (Magstim)",
            # One binary stim channel per event type (DIN1, DIN2, ...): reading only the first
            # would lose every other type. The annotations hold all of them.
            event_source="annotations",
            notes=notes,
        )

    @staticmethod
    def _read(path: Path) -> mne.io.BaseRaw:
        raw: mne.io.BaseRaw = mne.io.read_raw_egi(
            path, preload=True, events_as_annotations=True, verbose="error"
        )
        return raw
