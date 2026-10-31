"""Where every stage reads and writes (docs/BLUEPRINT_PHASES.md section 3).

Subject-level names are built with ``mne_bids.BIDSPath`` (never by hand) under
``derivatives/eegunify-<stage>/``. Group-level outputs live in
``derivatives/eegunify-group/task-<task>/``, which BIDSPath cannot express.
"""

from __future__ import annotations

from pathlib import Path

from mne_bids import BIDSPath

from eegunify.config.schema import Config

DERIVATIVES = "derivatives"
RAWDATA_SIDECARS = (
    "_eeg.vhdr",
    "_eeg.eeg",
    "_eeg.vmrk",
    "_eeg.json",
    "_channels.tsv",
    "_events.tsv",
)
"""Rawdata files whose content determines the ERP stages' results (hashed for the cache)."""


def stage_root(project_root: Path, stage: str) -> Path:
    return project_root / DERIVATIVES / f"eegunify-{stage}"


def stage_path(
    project_root: Path,
    stage: str,
    cfg: Config,
    subject: str,
    *,
    description: str,
    suffix: str,
    extension: str,
) -> Path:
    """``derivatives/eegunify-<stage>/sub-X/[ses-Y/]eeg/sub-X_..._desc-<d>_<suffix><ext>``."""
    bp = BIDSPath(
        root=stage_root(project_root, stage),
        subject=subject,
        session=cfg.recordings.session,
        task=cfg.dataset.task,
        run=cfg.recordings.run,
        datatype="eeg",
        description=description,
        suffix=suffix,
        extension=extension,
        check=False,
    )
    return Path(bp.fpath)


def subject_dir(project_root: Path, stage: str, cfg: Config, subject: str) -> Path:
    return stage_path(
        project_root, stage, cfg, subject, description=stage, suffix="x", extension=".x"
    ).parent


def provenance_path(project_root: Path, stage: str, cfg: Config, subject: str | None) -> Path:
    """The provenance JSON a stage run writes; it holds the ``stage_key``."""
    if subject is None:
        return group_dir(project_root, cfg) / "provenance.json"
    return stage_path(
        project_root, stage, cfg, subject, description=stage, suffix="provenance", extension=".json"
    )


def group_dir(project_root: Path, cfg: Config) -> Path:
    return stage_root(project_root, "group") / f"task-{cfg.dataset.task}"


def rawdata_files(project_root: Path, cfg: Config, subject: str) -> list[Path]:
    """The canonical rawdata files of one subject (as written by ``eegunify convert``)."""
    bp = BIDSPath(
        root=project_root / cfg.dataset.bids_root,
        subject=subject,
        session=cfg.recordings.session,
        task=cfg.dataset.task,
        run=cfg.recordings.run,
        datatype="eeg",
        check=False,
    )
    folder = Path(bp.directory)
    stem = bp.basename
    return [folder / f"{stem}{s}" for s in RAWDATA_SIDECARS if (folder / f"{stem}{s}").exists()]
