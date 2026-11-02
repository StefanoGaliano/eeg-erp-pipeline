"""Format-independent ingestion: file -> canonical :class:`Recording` -> BIDS."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mne
from mne.io.constants import FIFF

from eegunify import provenance
from eegunify.bids_writer import bids_path_for, write_recording
from eegunify.canonical import Recording, Violation, validate
from eegunify.channels.montage import apply_montage
from eegunify.channels.naming import normalize_names
from eegunify.channels.types import infer_type, infer_types
from eegunify.config.loader import load_config, resolve_for_subject
from eegunify.config.schema import Config
from eegunify.detect import detect_format
from eegunify.events.extract import extract_events
from eegunify.readers import get_reader
from eegunify.units import TO_VOLTS, check_units

log = logging.getLogger(__name__)


#: ``_eeg.json`` keys copied from a source that is already BIDS (OpenNeuro, ERP CORE). MNE-BIDS
#: would otherwise fill e.g. ``Manufacturer`` from the *output* format (BrainVision).
INHERITED_SIDECAR_KEYS = (
    "Manufacturer",
    "ManufacturersModelName",
    "InstitutionName",
    "InstitutionAddress",
    "TaskDescription",
    "Instructions",
    "CapManufacturer",
    "CapManufacturersModelName",
    "EEGPlacementScheme",
    "HardwareFilters",
)


def read_source_sidecar(path: Path) -> dict[str, Any]:
    """The source's own BIDS ``*_eeg.json`` (``sub-01_task-x_eeg.set`` -> ``..._eeg.json``)."""
    sidecar = path.with_suffix(".json")
    if not path.stem.endswith("_eeg") or not sidecar.is_file():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("could not parse source sidecar %s", sidecar.name)
        return {}
    return data if isinstance(data, dict) else {}


def default_config(path: Path, subject: str = "01") -> Config:
    """Minimal config for reading a single file without a YAML (used by ``inspect``)."""
    return Config.model_validate(
        {
            "dataset": {"name": path.stem, "source_root": str(path.parent), "task": "unknown"},
            "recordings": {"pattern": "{subject}", "subjects": [subject]},
        }
    )


def _channel_types(raw: mne.io.BaseRaw, rename_log: dict[str, str], cfg: Config) -> None:
    reader_types = dict(zip(raw.ch_names, raw.get_channel_types(), strict=True))
    types = infer_types(raw.ch_names, configured=cfg.channels.types, reader_types=reader_types)
    for name, rt in reader_types.items():
        # Trigger channels stay triggers: EGI event tracks are named by their code, and one
        # called "resp" (a response track, GABA infant VEP, Zenodo 5172962) would otherwise
        # become a respiration channel and be written as data.
        if rt == "stim" and name not in cfg.channels.types:
            types[name] = "stim"
    new_to_orig = {new: orig for orig, new in rename_log.items()}
    for name, ch_type in types.items():
        if ch_type == "misc" and name not in cfg.channels.types:
            # Prefixes like "EOG L" are stripped by naming; the original still says EOG.
            types[name] = infer_type(new_to_orig.get(name, name), reader_types.get(name))
    if "eeg" not in types.values():
        # No recognisable EEG names at all (e.g. a consumer headset with channels "1".."8"):
        # trust the reader's EEG typing rather than ending up with zero EEG channels.
        kept = [n for n, t in types.items() if t == "misc" and reader_types.get(n) == "eeg"]
        if kept:
            log.warning("no 10-05 channel names; keeping %d reader-typed EEG channels", len(kept))
            types.update(dict.fromkeys(kept, "eeg"))
    changed = {n: t for n, t in types.items() if t != reader_types[n]}
    if changed:
        units = {ch["ch_name"]: ch["unit"] for ch in raw.info["chs"]}
        raw.set_channel_types(changed, on_unit_change="ignore", verbose="error")
        # Re-typing to misc drops the unit; keep Volts for channels that were voltages
        # (BioSemi EXG mastoids/EOG) so they are written in µV, not as unitless numbers.
        for ch in raw.info["chs"]:
            if ch["ch_name"] in changed and units[ch["ch_name"]] == FIFF.FIFF_UNIT_V:
                ch["unit"] = FIFF.FIFF_UNIT_V


def read_recording(
    path: str | Path,
    cfg: Config | None = None,
    *,
    subject: str = "01",
    project_root: Path | None = None,
) -> tuple[Recording, list[Violation]]:
    """Read any supported file into a validated canonical :class:`Recording`.

    Returns
    -------
    recording, warnings
        The recording and the non-fatal invariant violations.

    Raises
    ------
    eegunify.canonical.CanonicalError
        If an invariant is broken (e.g. units off by 1e6, missing events).
    """
    path = Path(path)
    cfg = cfg or default_config(path, subject)
    project_root = project_root or Path.cwd()
    fmt = detect_format(path) if cfg.format.name == "auto" else cfg.format.name
    result = get_reader(fmt).read(path, cfg.format.hints)
    raw = result.raw
    source_sfreq = float(raw.info["sfreq"])

    # Stim channels keep their names (Status, STI 014) so event extraction can find them.
    reader_types = raw.get_channel_types()
    stim = [
        n
        for n, t in zip(raw.ch_names, reader_types, strict=True)
        if t == "stim" or infer_type(n) == "stim"
    ]
    if stim:
        raw.set_channel_types({n: "stim" for n in stim}, on_unit_change="ignore", verbose="error")
    rename_log = normalize_names(
        raw.ch_names,
        aliases_extra=cfg.channels.aliases_extra,
        rename=cfg.channels.rename,
        protect=stim,
    )
    raw.rename_channels({o: n for o, n in rename_log.items() if o != n})
    _channel_types(raw, rename_log, cfg)

    drop = [
        n
        for n in raw.ch_names
        if n in cfg.channels.drop
        or {v: k for k, v in rename_log.items()}.get(n) in cfg.channels.drop
    ]
    if drop:
        raw.drop_channels(drop)

    events = extract_events(
        raw,
        cfg.events,
        bdf_status_mask=result.status_mask,
        annotation_status_mask=cfg.format.hints.bdf_status_mask,
        source_path=path,
        preferred_source=result.event_source,
        extra_ignore_codes=result.ignore_codes,
    )
    behavior = None
    if cfg.behavior.source != "none":
        from eegunify.events.behavior import attach_behavior

        source_dir = project_root / cfg.dataset.source_root
        events.table, behavior = attach_behavior(events.table, cfg.behavior, source_dir, subject)

    bads = cfg.bads_for(subject)
    unknown = [b for b in bads if b not in raw.ch_names]
    if unknown:
        raise ValueError(f"sub-{subject}: bad channels not in recording: {unknown}")
    raw.info["bads"] = bads

    missing = apply_montage(raw, cfg.montage, path.parent, subject)
    unit_factor = 1.0 if cfg.units.declared == "auto" else TO_VOLTS[cfg.units.declared]
    if unit_factor != 1.0:
        volt_picks = [i for i, ch in enumerate(raw.info["chs"]) if ch["unit"] == FIFF.FIFF_UNIT_V]
        raw.apply_function(lambda x: x * unit_factor, picks=volt_picks, channel_wise=False)
        log.warning(
            "units.declared=%s: voltage channels rescaled by %g", cfg.units.declared, unit_factor
        )
    units = check_units(raw, cfg.units.expected_eeg_range_uV)

    source_sidecar = read_source_sidecar(path)
    reference = cfg.reference.online
    if reference == "n/a" and result.default_reference:
        reference = result.default_reference
    sidecar = {k: v for k, v in source_sidecar.items() if k in INHERITED_SIDECAR_KEYS}
    if "Manufacturer" not in sidecar:
        sidecar["Manufacturer"] = result.manufacturer or "n/a"
    line_freq = source_sidecar.get("PowerLineFrequency")
    if not isinstance(line_freq, (int, float)):
        from eegunify.qa.checks import detect_line_freq

        line_freq = detect_line_freq(raw)
    if line_freq:
        raw.info["line_freq"] = float(line_freq)

    rec = Recording(
        raw=raw,
        subject=subject,
        format=fmt,
        events=events,
        reference=reference,
        ground=cfg.reference.ground,
        units=units,
        rename_log=rename_log,
        missing_positions=missing,
        provenance=provenance.build(
            result.source_files,
            fmt=fmt,
            reader=result.reader,
            source_sfreq=source_sfreq,
            config=cfg.model_dump(mode="json"),
            rename_log=rename_log,
            event_method=events.method,
            project_root=project_root,
        )
        | {"unit_correction_factor": unit_factor},
        notes=result.notes,
        sidecar=sidecar,
        behavior=behavior,
    )
    warnings = validate(
        rec,
        montage_expected=cfg.montage.source != "none",
        expected_range_uV=cfg.units.expected_eeg_range_uV,
    )
    for w in warnings:
        log.warning("sub-%s [rule %d] %s", subject, w.rule, w.message)
    return rec, warnings


@dataclass
class ConvertResult:
    subject: str
    source: Path
    output: Path | None
    warnings: list[Violation]
    error: str | None = None
    qa_status: str | None = None
    """PASS / WARN / FAIL, or None when QA was skipped or the recording failed."""
    qa_report: Path | None = None


def source_path(cfg: Config, subject: str, project_root: Path) -> Path:
    return project_root / cfg.dataset.source_root / cfg.recordings.path_for(subject)


def _run_qa(rec: Recording, cfg: Config, project_root: Path) -> tuple[str, Path]:
    from eegunify.qa import run_checks
    from eegunify.qa.report import update_summary, write_report

    qa_root = project_root / "derivatives" / "eegunify-qa"
    basename = bids_path_for(cfg, rec.subject, project_root).basename.removesuffix("_eeg.vhdr")
    result = run_checks(rec, cfg)
    report = write_report(rec, result, qa_root / f"sub-{rec.subject}", basename)
    update_summary(qa_root / "summary.tsv", rec, result, basename, report)
    for check in result.checks:
        if check.level != "pass":
            log.warning(
                "sub-%s QA %s %s: %s", rec.subject, check.level.upper(), check.id, check.message
            )
    return result.status.upper(), report


def convert(
    config: str | Path | Config,
    *,
    subjects: list[str] | None = None,
    overwrite: bool = False,
    project_root: Path | None = None,
    qa: bool = True,
    write_bids: bool = True,
) -> list[ConvertResult]:
    """Convert every (or the selected) subject of a dataset config to BIDS, then run QA.

    With ``write_bids=False`` only QA runs (the ``eegunify qa`` command). A failing subject is
    reported in its :class:`ConvertResult` and does not stop the others.
    """
    project_root = (project_root or Path.cwd()).resolve()
    base = config if isinstance(config, Config) else load_config(config)
    wanted = subjects or base.recordings.subjects
    results = []
    for subject in wanted:
        cfg = resolve_for_subject(base, subject)
        src = source_path(cfg, subject, project_root)
        try:
            rec, warnings = read_recording(src, cfg, subject=subject, project_root=project_root)
            out = None
            if write_bids:
                bp = write_recording(
                    rec,
                    cfg,
                    bids_root=project_root / cfg.dataset.bids_root,
                    derivatives_root=project_root / "derivatives",
                    overwrite=overwrite,
                )
                out = Path(bp.fpath)
            res = ConvertResult(subject, src, out, warnings)
            if qa:
                res.qa_status, res.qa_report = _run_qa(rec, cfg, project_root)
            results.append(res)
        except Exception as err:  # reported per subject; the CLI sets the exit code
            log.error("sub-%s failed: %s", subject, err)
            results.append(ConvertResult(subject, src, None, [], f"{type(err).__name__}: {err}"))
    return results
