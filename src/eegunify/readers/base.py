"""Reader protocol and registry.

A reader only turns a file into a preloaded ``mne.io.Raw`` plus a few format facts. Everything
format-independent (names, types, events, montage, validation) happens afterwards in
:mod:`eegunify.pipeline`, so adding a format means adding one small module here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import mne

from eegunify.config.schema import FormatHints


@dataclass
class ReadResult:
    """What a reader returns."""

    raw: mne.io.BaseRaw
    source_files: list[Path]
    """Every file the recording consists of (hashed for provenance)."""
    reader: str
    """Fully-qualified MNE function used."""
    default_reference: str | None = None
    manufacturer: str | None = None
    """Hardware vendor implied by the file format (BDF -> BioSemi); None if not implied."""
    """Reference implied by the hardware (BioSemi: CMS/DRL), used if the config says n/a."""
    status_mask: int | None = None
    """Bit mask for stim channel values (BioSemi Status)."""
    notes: list[str] = field(default_factory=list)


class Reader(Protocol):
    name: str
    extensions: tuple[str, ...]

    def read(self, path: Path, hints: FormatHints) -> ReadResult: ...


REGISTRY: dict[str, Reader] = {}


def register(cls: type) -> type:
    """Class decorator adding an instance of the reader to :data:`REGISTRY`."""
    inst: Reader = cls()
    REGISTRY[inst.name] = inst
    return cls


def get_reader(name: str) -> Reader:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(f"no reader for format {name!r}; available: {sorted(REGISTRY)}") from None


def sidecars(path: Path, *suffixes: str) -> list[Path]:
    """``path`` plus existing sibling files with the given suffixes."""
    out = [path]
    for suf in suffixes:
        p = path.with_suffix(suf)
        if p.exists():
            out.append(p)
    return out


ReadFn = Callable[..., mne.io.BaseRaw]
