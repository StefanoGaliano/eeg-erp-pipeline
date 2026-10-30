"""Detect the recording format from the file name (and header bytes where needed)."""

from __future__ import annotations

from pathlib import Path

#: Extension -> format name. ``.cnt`` is resolved by :func:`_cnt_flavor`.
EXTENSIONS: dict[str, str] = {
    ".vhdr": "brainvision",
    ".bdf": "bdf",
    ".edf": "edf",
    ".set": "eeglab",
    ".xdf": "xdf",
    ".xdfz": "xdf",
    ".gdf": "gdf",
    ".cdt": "curry",
    ".dap": "curry",
    ".mff": "egi",
    ".csv": "csv",
}


class UnknownFormatError(ValueError):
    """The file's format cannot be determined."""


def _cnt_flavor(path: Path) -> str:
    """ANT Neuro ``.cnt`` files are RIFF containers; Neuroscan ones start with a version string."""
    with path.open("rb") as fh:
        magic = fh.read(4)
    return "ant" if magic in (b"RIFF", b"RF64") else "neuroscan"


def detect_format(path: str | Path) -> str:
    """Return the format name for ``path``.

    Raises
    ------
    UnknownFormatError
        For unknown extensions, compressed files (``.gz``) and companion files that are not
        the entry point (``.eeg``/``.vmrk`` of BrainVision, ``.fdt`` of EEGLAB).
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".gz":
        raise UnknownFormatError(f"{p.name}: compressed file; decompress it first")
    if suffix in (".eeg", ".vmrk"):
        raise UnknownFormatError(f"{p.name}: BrainVision companion file; point to the .vhdr")
    if suffix == ".fdt":
        raise UnknownFormatError(f"{p.name}: EEGLAB data file; point to the .set")
    if suffix == ".cnt":
        return _cnt_flavor(p)
    if suffix == ".raw" and p.is_file():
        return "egi"
    try:
        return EXTENSIONS[suffix]
    except KeyError:
        raise UnknownFormatError(f"{p.name}: unknown EEG file extension {suffix!r}") from None
