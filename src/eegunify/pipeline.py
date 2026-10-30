"""Format-independent ingestion: file -> canonical :class:`Recording` -> BIDS."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import mne
from mne.io.constants import FIFF

from eegunify import provenance
from eegunify.bids_writer import write_recording
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

    events = extract_events(raw, cfg.events, bdf_status_mask=result.status_mask)

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

    reference = cfg.reference.online
    if reference == "n/a" and result.default_reference:
        reference = result.default_reference

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
    )
    warnings = validate(rec, montage_expected=cfg.montage.source != "none")
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


def source_path(cfg: Config, subject: str, project_root: Path) -> Path:
    return project_root / cfg.dataset.source_root / cfg.recordings.pattern.format(subject=subject)


def convert(
    config: str | Path | Config,
    *,
    subjects: list[str] | None = None,
    overwrite: bool = False,
    project_root: Path | None = None,
) -> list[ConvertResult]:
    """Convert every (or the selected) subject of a dataset config to BIDS.

    A failing subject is reported in its :class:`ConvertResult` and does not stop the others.
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
            bp = write_recording(
                rec,
                cfg,
                bids_root=project_root / cfg.dataset.bids_root,
                derivatives_root=project_root / "derivatives",
                overwrite=overwrite,
            )
            results.append(ConvertResult(subject, src, Path(bp.fpath), warnings))
        except Exception as err:
            log.error("sub-%s failed: %s", subject, err)
            results.append(ConvertResult(subject, src, None, [], f"{type(err).__name__}: {err}"))
    return results
