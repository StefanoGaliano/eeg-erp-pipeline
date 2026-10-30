"""Electrode positions: standard montage, position file, or positions embedded in the file."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import mne

from eegunify.config.schema import MontageSection

log = logging.getLogger(__name__)

_RENAMED_MONTAGES = {"standard_1005": "colin27_1005", "standard_1020": "colin27_1020"}


def _standard(name: str) -> mne.channels.DigMontage:
    builtin = mne.channels.get_builtin_montages()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        if name in _RENAMED_MONTAGES and _RENAMED_MONTAGES[name] in builtin:
            return mne.channels.make_standard_montage(_RENAMED_MONTAGES[name])
        return mne.channels.make_standard_montage(name)


def read_montage_file(path: Path) -> mne.channels.DigMontage:
    """Read ``.elc/.sfp/.loc/...`` with ``read_custom_montage`` or BrainVision CapTrak ``.bvct``."""
    if path.suffix.lower() == ".bvct":
        return mne.channels.read_dig_captrak(path)
    return mne.channels.read_custom_montage(path)


def apply_montage(
    raw: mne.io.BaseRaw, cfg: MontageSection, source_dir: Path, subject: str
) -> list[str]:
    """Set channel positions on ``raw`` in place.

    Returns
    -------
    list of str
        EEG channels that have no position after applying the montage.
    """
    if cfg.source == "none":
        pass
    elif cfg.source == "embedded":
        if raw.get_montage() is None:
            msg = "montage.source is 'embedded' but the file carries no positions"
            if cfg.on_missing == "raise":
                raise ValueError(msg)
            log.warning(msg)
    else:
        if cfg.source == "file":
            assert cfg.file is not None  # guaranteed by the schema
            montage = read_montage_file(source_dir / cfg.file.format(subject=subject))
        else:
            montage = _standard(cfg.standard_name)
        raw.set_montage(montage, on_missing="ignore", match_case=False)

    eeg_idx = mne.pick_types(raw.info, eeg=True, exclude=[])
    missing = []
    for i in eeg_idx:
        loc = raw.info["chs"][i]["loc"][:3]
        if not (loc == loc).all() or not loc.any():  # NaN or all-zero
            missing.append(raw.ch_names[i])
    if missing and cfg.source != "none":
        msg = f"{len(missing)} EEG channel(s) without positions: {missing}"
        if cfg.on_missing == "raise":
            raise ValueError(msg)
        if cfg.on_missing == "warn":
            log.warning(msg)
    return missing
