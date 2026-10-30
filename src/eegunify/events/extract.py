"""Extract events from a stim channel or from annotations and attach them to the Raw.

After :func:`extract_events`, ``raw.annotations`` holds one annotation per kept event with
``description = trial_type`` and ``extras = {"code": <original code>}``, and every stim
channel has been dropped. The returned table is the source for ``events.tsv``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import mne
import numpy as np
import pandas as pd

from eegunify.config.schema import EventsSection
from eegunify.events.mapping import EventMapper, UnmappedEventError, parse_code

log = logging.getLogger(__name__)

#: Annotation descriptions that are recording housekeeping, not experimental events.
_HOUSEKEEPING = {"new segment/", "boundary", "edf annotations", "bad", "bad_acq_skip"}


@dataclass
class EventResult:
    """Events extracted from one recording."""

    table: pd.DataFrame
    """Columns: onset (s, relative to first sample), duration, sample, value, trial_type."""
    method: str
    """``stim_channel:<name>`` or ``annotations``."""
    unmapped_codes: list[int | str]


def _stim_channels(raw: mne.io.BaseRaw) -> list[str]:
    return [raw.ch_names[i] for i in mne.pick_types(raw.info, stim=True, exclude=[])]


def _from_stim(raw: mne.io.BaseRaw, ch: str, cfg: EventsSection, mask: int) -> pd.DataFrame:
    ev = mne.find_events(
        raw,
        stim_channel=ch,
        consecutive=False,
        min_duration=(cfg.min_duration_samples - 0.5) / raw.info["sfreq"],
        mask=mask,
        mask_type="and",
        initial_event=True,
        verbose="error",
    )
    sample = ev[:, 0] - raw.first_samp
    return pd.DataFrame(
        {
            "onset": sample / raw.info["sfreq"],
            "duration": 0.0,
            "sample": sample,
            "value": ev[:, 2].astype(object),
        }
    )


_STATUS_RE = re.compile(r"^STATUS/(\d+)$", re.IGNORECASE)


def _from_annotations(raw: mne.io.BaseRaw, status_mask: int) -> pd.DataFrame:
    ann = raw.annotations
    first_time = raw.first_samp / raw.info["sfreq"]
    rows = []
    for a in ann:
        desc = str(a["description"])
        if desc.strip().lower() in _HOUSEKEEPING or desc.upper().startswith("BAD"):
            continue
        # With orig_time set, onsets count from meas_date and include the first_samp offset;
        # without it they already count from the first sample.
        onset = float(a["onset"]) - (first_time if ann.orig_time is not None else 0.0)
        rows.append(
            {
                "onset": onset,
                "duration": float(a["duration"]),
                "sample": round(onset * raw.info["sfreq"]),
                "value": _annotation_code(desc, status_mask),
            }
        )
    return pd.DataFrame(rows, columns=["onset", "duration", "sample", "value"])


def _annotation_code(desc: str, status_mask: int) -> int | str:
    """``parse_code``, plus raw BioSemi Status values kept as markers (``STATUS/61694``, seen
    when BDF data were converted to BrainVision): masked like the Status channel itself."""
    m = _STATUS_RE.match(desc.strip())
    if m:
        return int(m.group(1)) & status_mask
    return parse_code(desc)


def bids_events_path(recording: Path) -> Path | None:
    """``sub-01_task-x_eeg.bdf`` -> sibling ``sub-01_task-x_events.tsv`` if it exists."""
    stem = recording.name.split(".")[0]
    if not stem.endswith("_eeg"):
        return None
    path = recording.with_name(stem.removesuffix("_eeg") + "_events.tsv")
    return path if path.is_file() else None


def _from_bids_events(path: Path, sfreq: float) -> pd.DataFrame:
    """Events from a BIDS ``events.tsv``: ``value`` if present, else ``trial_type``."""
    tsv = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    if "onset" not in tsv.columns:
        raise ValueError(f"{path.name} has no onset column")
    label_col = next((c for c in ("value", "trial_type", "event_type") if c in tsv.columns), None)
    rows = []
    for _, r in tsv.iterrows():
        try:
            onset = float(r["onset"])
        except ValueError:
            continue
        label = str(r[label_col]) if label_col else "event"
        if label in ("", "n/a"):
            label = str(r.get("trial_type", "event")) or "event"
        duration = r.get("duration", "0")
        rows.append(
            {
                "onset": onset,
                "duration": float(duration) if duration not in ("", "n/a") else 0.0,
                "sample": round(onset * sfreq),
                "value": parse_code(label),
            }
        )
    return pd.DataFrame(rows, columns=["onset", "duration", "sample", "value"])


def extract_events(
    raw: mne.io.BaseRaw,
    cfg: EventsSection,
    *,
    bdf_status_mask: int | None = None,
    annotation_status_mask: int = 0xFF,
    source_path: Path | None = None,
) -> EventResult:
    """Extract and map events, then rewrite ``raw.annotations`` and drop stim channels.

    Parameters
    ----------
    raw
        Preloaded raw (modified in place).
    cfg
        The ``events`` config block.
    bdf_status_mask
        Bit mask applied to stim channel values (BioSemi Status: ``0xFF``).
    annotation_status_mask
        Bit mask for ``STATUS/<n>`` markers (BioSemi Status values stored as annotations).
    source_path
        The recording file, used to find a sibling BIDS ``*_events.tsv``.

    With ``events.source: auto`` the stim channel is used if there is one, unless it yields no
    events while the file has markers: a Status channel stored as a scaled voltage (BDF
    converted to BrainVision, seen in ds001810) decodes to nothing.
    """
    stim = _stim_channels(raw)
    source = cfg.source
    if source == "auto":
        source = "stim_channel" if stim else "annotations"
    if source == "stim_channel":
        ch = cfg.stim_channel or (stim[0] if stim else None)
        if ch is None or ch not in raw.ch_names:
            raise ValueError(f"events.source is stim_channel but no stim channel found ({ch!r})")
        mask = bdf_status_mask if bdf_status_mask is not None else 0xFFFFFF
        table = _from_stim(raw, ch, cfg, mask)
        method = f"stim_channel:{ch}"
        if table.empty and cfg.source == "auto":
            markers = _from_annotations(raw, annotation_status_mask)
            if not markers.empty:
                log.warning(
                    "stim channel %s has no events; using the %d markers instead", ch, len(markers)
                )
                table, method = markers, "annotations"
    elif source == "annotations":
        table = _from_annotations(raw, annotation_status_mask)
        method = "annotations"
    elif source == "bids_events":
        tsv = bids_events_path(source_path) if source_path else None
        if tsv is None:
            raise ValueError("events.source is bids_events but no sibling *_events.tsv exists")
        table, method = _from_bids_events(tsv, raw.info["sfreq"]), f"bids_events:{tsv.name}"
    else:
        raise NotImplementedError(f"events.source {source!r} is not available for this format yet")

    # Recordings without triggers whose events live only in the BIDS sidecar (ds003645,
    # ds006768): fall back to it in auto mode.
    if table.empty and cfg.source == "auto" and source_path is not None:
        tsv = bids_events_path(source_path)
        if tsv is not None:
            table = _from_bids_events(tsv, raw.info["sfreq"])
            method = f"bids_events:{tsv.name}"
            log.warning("no events in the recording; using %s (%d events)", tsv.name, len(table))

    mapper = EventMapper.from_config(cfg.mapping)
    ignore = set(cfg.ignore_codes)
    keep, trial_types, unmapped = [], [], []
    for value in table["value"]:
        if isinstance(value, (int, np.integer)) and int(value) in ignore:
            keep.append(False)
            trial_types.append("")
            continue
        tt = mapper.lookup(value) if cfg.mapping else None
        if tt is None:
            if value not in unmapped:
                unmapped.append(value)
            if cfg.unmapped == "error":
                raise UnmappedEventError(f"event code {value!r} has no mapping")
            if cfg.unmapped == "drop":
                keep.append(False)
                trial_types.append("")
                continue
            tt = str(value)
        keep.append(True)
        trial_types.append(tt)
    # Boolean array, not list: an empty list would select zero *columns* in pandas.
    table = table.assign(trial_type=trial_types)[np.asarray(keep, dtype=bool)]
    table = table.reset_index(drop=True)
    table = table.sort_values("onset", kind="stable").reset_index(drop=True)
    if unmapped and cfg.mapping:
        log.warning("unmapped event codes (%s): %s", cfg.unmapped, unmapped)

    offset = raw.first_samp / raw.info["sfreq"] if raw.info["meas_date"] is not None else 0.0
    raw.set_annotations(
        mne.Annotations(
            onset=table["onset"].to_numpy(dtype=float) + offset,
            duration=table["duration"].to_numpy(dtype=float),
            description=table["trial_type"].astype(str).tolist(),
            orig_time=raw.info["meas_date"],
            extras=[{"code": str(v)} for v in table["value"]],
        )
    )
    if stim:
        raw.drop_channels(stim)
    return EventResult(table=table, method=method, unmapped_codes=unmapped)
