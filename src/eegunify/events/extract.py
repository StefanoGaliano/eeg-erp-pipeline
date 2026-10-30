"""Extract events from a stim channel or from annotations and attach them to the Raw.

After :func:`extract_events`, ``raw.annotations`` holds one annotation per kept event with
``description = trial_type`` and ``extras = {"code": <original code>}``, and every stim
channel has been dropped. The returned table is the source for ``events.tsv``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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


def _from_annotations(raw: mne.io.BaseRaw) -> pd.DataFrame:
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
                "value": parse_code(desc),
            }
        )
    return pd.DataFrame(rows, columns=["onset", "duration", "sample", "value"])


def extract_events(
    raw: mne.io.BaseRaw, cfg: EventsSection, *, bdf_status_mask: int | None = None
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
    elif source == "annotations":
        table = _from_annotations(raw)
        method = "annotations"
    else:
        raise NotImplementedError(f"events.source {source!r} is not available for this format yet")

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
