"""LSL XDF (``.xdf``): one EEG stream plus an optional marker stream.

XDF holds several streams recorded by different programs, each with its own timestamps.
``pyxdf`` applies the LSL clock offsets (so a marker stream from another computer lines up with
the EEG) and removes timestamp jitter. Markers become annotations relative to the first EEG
sample. If the measured sampling rate deviates from the nominal one by more than 0.1 %, the EEG
is interpolated onto a uniform grid at the nominal rate (logged as a note).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import mne
import numpy as np

from eegunify.config.schema import FormatHints
from eegunify.readers.base import ReadResult, register

log = logging.getLogger(__name__)

_UNIT_TO_VOLTS = {
    "microvolts": 1e-6,
    "microvolt": 1e-6,
    "uv": 1e-6,
    "µv": 1e-6,
    "μv": 1e-6,
    "millivolts": 1e-3,
    "millivolt": 1e-3,
    "mv": 1e-3,
    "volts": 1.0,
    "volt": 1.0,
    "v": 1.0,
}
_TYPE_MAP = {
    "eeg": "eeg",
    "eog": "eog",
    "emg": "emg",
    "ecg": "ecg",
    "ekg": "ecg",
    "stim": "stim",
    "trigger": "stim",
    "misc": "misc",
    "aux": "misc",
}
MAX_RATE_DEVIATION = 0.001


def _first(info: dict[str, Any], key: str, default: str = "") -> str:
    value = info.get(key)
    if isinstance(value, list) and value:
        return str(value[0]) if value[0] is not None else default
    return default


def _matches(stream: dict[str, Any], selector: dict[str, str]) -> bool:
    info = stream["info"]
    return all(_first(info, k).lower() == v.lower() for k, v in selector.items())


def _channels(stream: dict[str, Any]) -> list[dict[str, str]]:
    info = stream["info"]
    n = int(_first(info, "channel_count", "0"))
    try:
        chans = info["desc"][0]["channels"][0]["channel"]
        out = [{k: _first(c, k) for k in ("label", "unit", "type")} for c in chans]
    except (KeyError, IndexError, TypeError):
        out = []
    if len(out) != n:
        out = [{"label": f"Ch{i + 1}", "unit": "", "type": ""} for i in range(n)]
    return out


def _select(
    streams: list[dict[str, Any]], selector: dict[str, str], what: str, required: bool
) -> dict[str, Any] | None:
    hits = [s for s in streams if _matches(s, selector)]
    if len(hits) > 1:
        names = [_first(s["info"], "name") for s in hits]
        raise ValueError(
            f"{len(hits)} {what} streams match {selector}: {names}; "
            f"set format.hints.xdf_{what.lower()}_stream to e.g. {{name: {names[0]!r}}}"
        )
    if not hits:
        if required:
            available = [(_first(s["info"], "name"), _first(s["info"], "type")) for s in streams]
            raise ValueError(f"no {what} stream matches {selector}; streams: {available}")
        return None
    return hits[0]


@register
class XdfReader:
    name = "xdf"
    extensions = (".xdf", ".xdfz")

    def read(self, path: Path, hints: FormatHints) -> ReadResult:
        import pyxdf

        streams, _ = pyxdf.load_xdf(
            str(path),
            synchronize_clocks=True,
            dejitter_timestamps=True,
            handle_clock_resets=True,
            verbose=False,
        )
        eeg = _select(streams, hints.xdf_eeg_stream, "EEG", required=True)
        assert eeg is not None
        markers = _select(streams, hints.xdf_marker_stream, "marker", required=False)
        notes: list[str] = []

        info = eeg["info"]
        nominal = float(_first(info, "nominal_srate", "0"))
        ts = np.asarray(eeg["time_stamps"], dtype=float)
        data = np.asarray(eeg["time_series"], dtype=float).T  # (n_channels, n_samples)
        if nominal <= 0:
            raise ValueError("EEG stream has an irregular sampling rate (nominal_srate 0)")
        effective = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) > 1 else nominal
        if abs(effective - nominal) / nominal > MAX_RATE_DEVIATION:
            grid = ts[0] + np.arange(int((ts[-1] - ts[0]) * nominal) + 1) / nominal
            data = np.vstack([np.interp(grid, ts, row) for row in data])
            notes.append(
                f"effective rate {effective:.3f} Hz deviates > 0.1 % from nominal {nominal:g} Hz;"
                " resampled onto a uniform grid"
            )
            log.warning(notes[-1])
        t0 = ts[0]

        chans = _channels(eeg)
        names, types = [], []
        for i, ch in enumerate(chans):
            names.append(ch["label"] or f"Ch{i + 1}")
            types.append(_TYPE_MAP.get(ch["type"].lower(), "eeg"))
            unit = ch["unit"].lower()
            if types[-1] == "stim":
                continue
            if unit in _UNIT_TO_VOLTS:
                data[i] *= _UNIT_TO_VOLTS[unit]
            else:
                # LSL convention: EEG amplifiers stream µV. The canonical unit check catches it
                # if this is wrong.
                data[i] *= 1e-6
        if any(c["unit"].lower() not in _UNIT_TO_VOLTS for c in chans):
            notes.append("some channels have no unit in the stream header; assumed µV")

        raw = mne.io.RawArray(data, mne.create_info(names, nominal, types), verbose="error")
        if markers is not None and len(markers["time_stamps"]):
            m_ts = np.asarray(markers["time_stamps"], dtype=float) - t0
            labels = [
                str(v[0]) if isinstance(v, (list, np.ndarray)) else str(v)
                for v in markers["time_series"]
            ]
            inside = (m_ts >= 0) & (m_ts <= raw.times[-1])
            if (~inside).any():
                notes.append(f"{int((~inside).sum())} markers outside the EEG recording dropped")
            raw.set_annotations(
                mne.Annotations(
                    m_ts[inside],
                    np.zeros(int(inside.sum())),
                    [lab for lab, keep in zip(labels, inside, strict=True) if keep],
                )
            )
        stream_names = [
            f"{_first(s['info'], 'name')} ({_first(s['info'], 'type')})" for s in streams
        ]
        notes.append(f"XDF streams: {', '.join(stream_names)}")
        return ReadResult(raw, [path], "pyxdf.load_xdf", notes=notes)
