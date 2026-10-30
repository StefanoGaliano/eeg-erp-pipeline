"""Write a canonical :class:`~eegunify.canonical.Recording` as BIDS-EEG (BrainVision payload).

MNE-BIDS does the heavy lifting (BrainVision float32 in µV, sidecars). We then enrich:

* ``events.tsv``: ``value`` holds the *original* event code (MNE-BIDS would renumber);
* ``channels.tsv``: ``original_name`` column from the rename log;
* ``_eeg.json``: ``EEGReference``/``EEGGround`` and a short ``eegunify`` provenance block.

The full provenance goes to ``derivatives/eegunify-qa/sub-*/..._provenance.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import mne_bids
import pandas as pd

from eegunify import __version__
from eegunify.canonical import Recording
from eegunify.config.schema import Config

log = logging.getLogger(__name__)


def bids_path_for(cfg: Config, subject: str, bids_root: Path) -> mne_bids.BIDSPath:
    return mne_bids.BIDSPath(
        subject=subject,
        session=cfg.recordings.session,
        task=cfg.dataset.task,
        run=cfg.recordings.run,
        datatype="eeg",
        suffix="eeg",
        extension=".vhdr",
        root=bids_root,
    )


def _patch_events(path: Path, rec: Recording) -> None:
    if not path.exists():
        return
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    table = rec.events.table
    if len(df) != len(table):
        raise RuntimeError(
            f"{path.name}: MNE-BIDS wrote {len(df)} events but {len(table)} were extracted"
        )
    df["value"] = [str(v) for v in table["value"]]
    df["trial_type"] = table["trial_type"].astype(str).to_numpy()
    # MNE-BIDS also writes annotation extras as columns; ``code`` is already in ``value``.
    df = df.drop(columns=[c for c in ("code",) if c in df.columns])
    df.to_csv(path, sep="\t", index=False, na_rep="n/a")


def _patch_channels(path: Path, rec: Recording) -> None:
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    new_to_orig = {new: orig for orig, new in rec.rename_log.items()}
    df["original_name"] = [new_to_orig.get(n, n) for n in df["name"]]
    df.to_csv(path, sep="\t", index=False, na_rep="n/a")


def _patch_sidecar(path: Path, rec: Recording) -> None:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data.update(rec.sidecar)
    data["EEGReference"] = rec.reference
    if rec.ground:
        data["EEGGround"] = rec.ground
    prov = rec.provenance
    data["eegunify"] = {
        "version": __version__,
        "source_format": rec.format,
        "reader": prov.get("reader"),
        "source_files": [
            {"name": Path(f["path"]).name, "sha256": f["sha256"]}
            for f in prov.get("source_files", [])
        ],
        "event_extraction": prov.get("event_extraction"),
    }
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


def write_recording(
    rec: Recording,
    cfg: Config,
    *,
    bids_root: Path,
    derivatives_root: Path,
    overwrite: bool = False,
) -> mne_bids.BIDSPath:
    """Write one recording; refuses to overwrite existing output unless ``overwrite``."""
    bp = bids_path_for(cfg, rec.subject, bids_root)
    if bp.fpath.exists() and not overwrite:
        raise FileExistsError(f"{bp.fpath} exists; pass overwrite=True (--overwrite) to replace")

    anonymize = cfg.dataset.anonymize.model_dump() if cfg.dataset.anonymize else None
    mne_bids.write_raw_bids(
        rec.raw,
        bp,
        format="BrainVision",
        allow_preload=True,
        anonymize=anonymize,
        # One electrodes.tsv per task: a subject with several tasks would otherwise collide
        # on a single per-subject file (e.g. ds005841, 6 tasks per subject).
        electrodes_tsv_task=True,
        overwrite=overwrite,
        verbose="error",
    )
    mne_bids.make_dataset_description(
        path=bids_root,
        name=cfg.dataset.name,
        authors=cfg.dataset.authors or None,
        data_license=cfg.dataset.license,
        generated_by=[{"Name": "eegunify", "Version": __version__}],
        overwrite=True,
        verbose="error",
    )

    _patch_events(bp.copy().update(suffix="events", extension=".tsv").fpath, rec)
    _patch_channels(bp.copy().update(suffix="channels", extension=".tsv").fpath, rec)
    _patch_sidecar(bp.copy().update(suffix="eeg", extension=".json").fpath, rec)

    prov_path = (
        derivatives_root
        / "eegunify-qa"
        / f"sub-{rec.subject}"
        / (bp.basename.replace("_eeg.vhdr", "_provenance.json"))
    )
    prov_path.parent.mkdir(parents=True, exist_ok=True)
    prov_path.write_text(json.dumps(rec.provenance, indent=2, default=str) + "\n", "utf-8")
    log.info("wrote %s", bp.fpath)
    return bp
