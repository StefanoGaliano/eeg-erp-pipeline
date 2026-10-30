"""Provenance: what was converted, how, and with which software."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

_PACKAGES = ("eegunify", "mne", "mne-bids", "numpy", "scipy", "pybv", "edfio", "eeglabio")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 of a file (or of all files in a directory, e.g. EGI ``.mff``)."""
    h = hashlib.sha256()
    paths = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for p in paths:
        with p.open("rb") as fh:
            while block := fh.read(chunk):
                h.update(block)
    return h.hexdigest()


def package_versions() -> dict[str, str]:
    out = {}
    for pkg in _PACKAGES:
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "not installed"
    return out


def git_state(cwd: Path) -> dict[str, Any]:
    """Commit hash and whether the working tree is dirty (``None`` outside a git repo)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def build(
    source_files: list[Path],
    *,
    fmt: str,
    reader: str,
    source_sfreq: float,
    config: dict[str, Any],
    rename_log: dict[str, str],
    event_method: str,
    project_root: Path,
) -> dict[str, Any]:
    """Assemble the provenance record for one recording."""
    return {
        "source_files": [
            {
                "path": str(p),
                "sha256": sha256(p),
                "bytes": p.stat().st_size if p.is_file() else None,
            }
            for p in source_files
        ],
        "format": fmt,
        "reader": reader,
        "source_sfreq": source_sfreq,
        "versions": package_versions(),
        "git": git_state(project_root),
        "config": config,
        "channel_renames": {k: v for k, v in rename_log.items() if k != v},
        "event_extraction": event_method,
        "converted_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "platform": platform.platform(),
    }
