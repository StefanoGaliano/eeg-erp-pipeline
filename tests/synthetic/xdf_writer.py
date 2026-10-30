"""Minimal XDF 1.0 writer for tests (https://github.com/sccn/xdf/wiki/Specifications).

Writes an EEG stream (float32) and an optional string marker stream, each with ClockOffset
chunks, so readers have to apply LSL clock synchronization and dejittering like on real data.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np


def _varlen(n: int) -> bytes:
    if n < 256:
        return struct.pack("<BB", 1, n)
    if n < 2**32:
        return struct.pack("<BI", 4, n)
    return struct.pack("<BQ", 8, n)


def _chunk(tag: int, content: bytes) -> bytes:
    body = struct.pack("<H", tag) + content
    return _varlen(len(body)) + body


@dataclass
class Stream:
    stream_id: int
    name: str
    type: str
    nominal_srate: float
    channel_format: str  # "float32" or "string"
    timestamps: np.ndarray  # in the *recording machine's* clock
    data: np.ndarray | Sequence[str]  # (n_samples, n_channels) floats, or marker strings
    labels: Sequence[str] = ()
    units: Sequence[str] = ()
    types: Sequence[str] = ()
    #: (collection_time, offset) pairs; true time = timestamp + offset.
    clock_offsets: list[tuple[float, float]] = field(default_factory=list)

    @property
    def channel_count(self) -> int:
        return 1 if self.channel_format == "string" else int(np.asarray(self.data).shape[1])

    def header(self) -> bytes:
        chans = "".join(
            f"<channel><label>{escape(lab)}</label><unit>{escape(unit)}</unit>"
            f"<type>{escape(t)}</type></channel>"
            for lab, unit, t in zip(self.labels, self.units, self.types, strict=True)
        )
        xml = (
            '<?xml version="1.0"?><info>'
            f"<name>{escape(self.name)}</name><type>{escape(self.type)}</type>"
            f"<channel_count>{self.channel_count}</channel_count>"
            f"<nominal_srate>{self.nominal_srate}</nominal_srate>"
            f"<channel_format>{self.channel_format}</channel_format>"
            f"<source_id>{self.name}-src</source_id><created_at>0</created_at>"
            f"<desc><channels>{chans}</channels></desc></info>"
        )
        return struct.pack("<I", self.stream_id) + xml.encode()

    def samples(self, start: int, stop: int) -> bytes:
        out = [struct.pack("<I", self.stream_id), _varlen(stop - start)]
        for i in range(start, stop):
            out.append(struct.pack("<Bd", 8, float(self.timestamps[i])))
            if self.channel_format == "string":
                text = str(self.data[i]).encode()
                out.append(_varlen(len(text)) + text)
            else:
                out.append(np.asarray(self.data[i], dtype="<f4").tobytes())
        return b"".join(out)

    def footer(self) -> bytes:
        ts = self.timestamps
        xml = (
            '<?xml version="1.0"?><info>'
            f"<first_timestamp>{ts[0]}</first_timestamp><last_timestamp>{ts[-1]}</last_timestamp>"
            f"<sample_count>{len(ts)}</sample_count></info>"
        )
        return struct.pack("<I", self.stream_id) + xml.encode()


def write_xdf(path: Path, streams: Sequence[Stream], chunk_size: int = 512) -> Path:
    parts = [b"XDF:", _chunk(1, b'<?xml version="1.0"?><info><version>1.0</version></info>')]
    parts += [_chunk(2, s.header()) for s in streams]
    for s in streams:
        for start in range(0, len(s.timestamps), chunk_size):
            parts.append(_chunk(3, s.samples(start, min(start + chunk_size, len(s.timestamps)))))
        for collected, offset in s.clock_offsets:
            parts.append(_chunk(4, struct.pack("<Idd", s.stream_id, collected, offset)))
    parts += [_chunk(6, s.footer()) for s in streams]
    path.write_bytes(b"".join(parts))
    return path
