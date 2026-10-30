"""BrainVision (``.vhdr`` + ``.vmrk`` + ``.eeg``)."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

import mne

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register

log = logging.getLogger(__name__)
_KEY_RE = r"^{key}=(.+)$"


def _repair_header(vhdr: Path, tmpdir: Path) -> tuple[Path, list[Path], list[str]]:
    """Point DataFile/MarkerFile at existing files named after the header, if they are missing.

    Datasets renamed after recording (common on OpenNeuro) keep the old names inside the
    header. The source is never edited: a corrected copy of the .vhdr (and .vmrk) is written
    to ``tmpdir`` with absolute paths.
    """
    text = vhdr.read_text(encoding="latin-1")
    used: list[Path] = [vhdr]
    notes: list[str] = []
    for key, suffix in (("DataFile", ".eeg"), ("MarkerFile", ".vmrk")):
        m = re.search(_KEY_RE.format(key=key), text, flags=re.MULTILINE)
        if not m:
            continue
        named = vhdr.parent / m.group(1).strip()
        target = named if named.exists() else vhdr.with_suffix(suffix)
        if not target.exists():
            continue  # let MNE raise its own, clear error
        if target != named:
            notes.append(f"{vhdr.name}: {key} {named.name!r} missing; used {target.name!r}")
            log.warning(notes[-1])
        if key == "MarkerFile":
            vmrk_text = target.read_text(encoding="latin-1")
            data_line = f"DataFile={_resolved(text, vhdr)}"
            vmrk_text = re.sub(
                _KEY_RE.format(key="DataFile"),
                lambda _m, line=data_line: line,  # type: ignore[misc]
                vmrk_text,
                flags=re.MULTILINE,
            )
            copy = tmpdir / target.name
            copy.write_text(vmrk_text, encoding="latin-1")
            text = text.replace(m.group(0), f"{key}={copy}")
        else:
            text = text.replace(m.group(0), f"{key}={target.resolve()}")
        used.append(target)
    fixed = tmpdir / vhdr.name
    fixed.write_text(text, encoding="latin-1")
    return fixed, used, notes


def _resolved(vhdr_text: str, vhdr: Path) -> str:
    m = re.search(_KEY_RE.format(key="DataFile"), vhdr_text, flags=re.MULTILINE)
    named = vhdr.parent / m.group(1).strip() if m else vhdr.with_suffix(".eeg")
    return str((named if named.exists() else vhdr.with_suffix(".eeg")).resolve())


def _referenced_files(vhdr: Path) -> list[Path]:
    """Data and marker files named inside the header (they may differ from the vhdr stem)."""
    text = vhdr.read_text(encoding="latin-1", errors="replace")
    out = [vhdr]
    for key in ("DataFile", "MarkerFile"):
        m = re.search(rf"^{key}=(.+)$", text, flags=re.MULTILINE)
        if m:
            p = vhdr.parent / m.group(1).strip()
            if p.exists():
                out.append(p)
    return out


@register
class BrainVisionReader:
    name = "brainvision"
    extensions = (".vhdr",)

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        files = _referenced_files(path)
        if len(files) == 3:
            raw = mne.io.read_raw_brainvision(path, preload=True, verbose="error")
            return ReadResult(
                raw, files, "mne.io.read_raw_brainvision", manufacturer="Brain Products"
            )
        with tempfile.TemporaryDirectory(prefix="eegunify-vhdr-") as tmp:
            fixed, files, notes = _repair_header(path, Path(tmp))
            raw = mne.io.read_raw_brainvision(fixed, preload=True, verbose="error")
        return ReadResult(
            raw, files, "mne.io.read_raw_brainvision", manufacturer="Brain Products", notes=notes
        )
