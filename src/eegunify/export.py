"""Export canonical BIDS recordings for MATLAB users (EEGLAB ``.set``).

The export reads what was *written* (``rawdata/``), not the source, so MATLAB sees exactly the
canonical data. EEGLAB event ``type`` = ``trial_type``; the original code is kept in the
``code`` field written by ``matlab/load_canonical.m`` when loading BrainVision directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

import mne
import mne_bids

from eegunify.bids_writer import bids_path_for
from eegunify.config.schema import Config

log = logging.getLogger(__name__)


def export_eeglab(cfg: Config, subject: str, project_root: Path, overwrite: bool = False) -> Path:
    """Write ``derivatives/eegunify-eeglab/sub-*/<basename>_eeg.set`` for one subject."""
    bp = bids_path_for(cfg, subject, project_root / cfg.dataset.bids_root)
    if not bp.fpath.exists():
        raise FileNotFoundError(f"{bp.fpath} not found: run `eegunify convert` first")
    raw = mne_bids.read_raw_bids(bp, verbose="error")
    raw.load_data()
    out_dir = project_root / "derivatives" / "eegunify-eeglab" / f"sub-{subject}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out: Path = out_dir / str(bp.basename).replace("_eeg.vhdr", "_eeg.set")
    if out.exists() and not overwrite:
        raise FileExistsError(f"{out} exists; pass overwrite=True (--overwrite) to replace")
    mne.export.export_raw(out, raw, fmt="eeglab", overwrite=overwrite, verbose="error")
    log.info("wrote %s", out)
    return out
