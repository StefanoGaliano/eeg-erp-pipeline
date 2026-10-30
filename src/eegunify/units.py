"""Unit handling and sanity heuristics.

MNE keeps EEG in Volts. A file whose header lies about its unit (common: data in µV labelled
as V, or the reverse) shows up as an implausible amplitude. We look at the robust spread of the
EEG channels and say which unit scaling would bring it into the physiological range.
"""

from __future__ import annotations

from dataclasses import dataclass

import mne
import numpy as np

#: Multiplicative factors to convert a value in the given unit to Volts.
TO_VOLTS = {"V": 1.0, "mV": 1e-3, "uV": 1e-6}


@dataclass(frozen=True)
class UnitVerdict:
    """Result of :func:`check_units`."""

    robust_sd_uV: float
    """Median over EEG channels of the robust SD (1.4826 * MAD), in µV."""
    plausible: bool
    """Whether ``robust_sd_uV`` lies inside the expected range."""
    suggested_factor: float
    """Factor to multiply the data by to make it plausible (1.0 if already plausible)."""
    explanation: str


def robust_sd_uV(raw: mne.io.BaseRaw, max_seconds: float = 60.0) -> float:
    """Median robust SD of good EEG channels, in µV (uses at most ``max_seconds`` of data)."""
    picks = mne.pick_types(raw.info, eeg=True, exclude="bads")
    if len(picks) == 0:
        return float("nan")
    stop = min(raw.n_times, int(max_seconds * raw.info["sfreq"]))
    data = raw.get_data(picks=picks, start=0, stop=stop)
    data = data - np.median(data, axis=1, keepdims=True)
    mad = np.median(np.abs(data), axis=1) * 1.4826
    mad = mad[mad > 0]  # flat channels are a separate QA check
    if mad.size == 0:
        return 0.0
    return float(np.median(mad) * 1e6)


def check_units(raw: mne.io.BaseRaw, expected_range_uV: tuple[float, float]) -> UnitVerdict:
    """Check that EEG amplitudes are physiologically plausible.

    Tries factors 1, 1e-3 and 1e-6 (and their inverses) and suggests the one that moves the
    robust SD into ``expected_range_uV``.
    """
    lo, hi = expected_range_uV
    sd = robust_sd_uV(raw)
    if not np.isfinite(sd) or sd == 0:
        return UnitVerdict(sd, False, 1.0, "no usable EEG signal to assess units")
    if lo <= sd <= hi:
        return UnitVerdict(sd, True, 1.0, f"robust SD {sd:.3g} µV within [{lo}, {hi}] µV")
    for factor, why in (
        (1e-6, "data look like µV stored as V"),
        (1e-3, "data look like mV stored as V"),
        (1e3, "data look like V stored as mV"),
        (1e6, "data look like V stored as µV"),
    ):
        if lo <= sd * factor <= hi:
            return UnitVerdict(sd, False, factor, f"robust SD {sd:.3g} µV; {why}")
    return UnitVerdict(sd, False, 1.0, f"robust SD {sd:.3g} µV outside [{lo}, {hi}] µV")
