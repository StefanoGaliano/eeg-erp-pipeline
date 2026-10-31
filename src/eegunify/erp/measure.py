"""ERP measurements (Phase 5, docs/BLUEPRINT_PHASES.md section 6.5).

Every function takes ``(times_ms, data_uv, window_ms)``: a 1-D waveform in µV, its time axis in
ms and a ``[start, end]`` window in ms. The window is inclusive and snaps to the nearest samples
(ERPLAB ``pop_geterpvalues`` does the same). Results are µV and ms.

- :func:`mean_amplitude`: mean of the samples in the window.
- :func:`peak`: local peak in the polarity direction, as ERPLAB's "local peak": a sample more
  extreme than both of its immediate neighbours and than the mean of the ``neighborhood``
  samples on each side (neighbours may lie outside the window). The most extreme such sample
  wins. When the window holds none, the absolute extreme is returned with ``local=False`` (QA
  ``MEASURE_NO_LOCAL_PEAK``).
- :func:`fractional_area_latency`: ERPLAB ``areaerp`` ported: the sample at which the area
  in the polarity direction (negative parts only for a negative component, trapezoid rule)
  reaches ``fraction`` of its total, found by ERPLAB's binary search. A symmetric triangle
  with its apex on a sample gives the apex exactly.

Pooled ROIs are averaged into one waveform before measuring (:func:`pool`, ERPLAB-like).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Polarity = Literal["positive", "negative"]


class MeasureError(ValueError):
    """A window or waveform that can't be measured (outside the epoch, empty)."""


def window_slice(times_ms: np.ndarray, window_ms: tuple[float, float]) -> slice:
    lo, hi = window_ms
    if lo < times_ms[0] - 1e-9 or hi > times_ms[-1] + 1e-9:
        raise MeasureError(
            f"window {lo:g}-{hi:g} ms outside the epoch ({times_ms[0]:g}-{times_ms[-1]:g} ms)"
        )
    i0 = int(np.argmin(np.abs(times_ms - lo)))
    i1 = int(np.argmin(np.abs(times_ms - hi)))
    if i1 < i0:
        raise MeasureError(f"window {lo:g}-{hi:g} ms holds no sample")
    return slice(i0, i1 + 1)


def mean_amplitude(
    times_ms: np.ndarray, data_uv: np.ndarray, window_ms: tuple[float, float]
) -> float:
    return float(np.mean(data_uv[window_slice(times_ms, window_ms)]))


@dataclass(frozen=True)
class Peak:
    amplitude_uv: float
    latency_ms: float
    local: bool


def peak(
    times_ms: np.ndarray,
    data_uv: np.ndarray,
    window_ms: tuple[float, float],
    polarity: Polarity,
    neighborhood: int = 3,
) -> Peak:
    sl = window_slice(times_ms, window_ms)
    sign = 1.0 if polarity == "positive" else -1.0
    x = sign * np.asarray(data_uv, dtype=float)
    n = max(int(neighborhood), 1)
    best: int | None = None
    for i in range(sl.start, sl.stop):
        if i - 1 < 0 or i + 1 >= x.size:
            continue
        left = x[max(i - n, 0) : i]
        right = x[i + 1 : i + 1 + n]
        is_local = x[i] > max(x[i - 1], x[i + 1], left.mean(), right.mean())
        if is_local and (best is None or x[i] > x[best]):
            best = i
    local = best is not None
    if best is None:
        best = sl.start + int(np.argmax(x[sl]))
    return Peak(float(data_uv[best]), float(times_ms[best]), local)


def _trapz(x: np.ndarray) -> float:
    """MATLAB ``trapz`` with unit spacing (0 for a single sample); numpy 1.26 has no
    ``trapezoid``."""
    return float(x.sum() - (x[0] + x[-1]) / 2) if x.size > 1 else 0.0


def fractional_area_latency(
    times_ms: np.ndarray,
    data_uv: np.ndarray,
    window_ms: tuple[float, float],
    polarity: Polarity,
    fraction: float = 0.5,
) -> float:
    """Port of ERPLAB ``areaerp`` (13.10) for a fixed window: the area in the polarity
    direction by the trapezoid rule, in single precision, then a binary search for the sample
    whose cumulative area matches ``fraction`` of it. The result is a sample time, as in ERPLAB.
    ``nan`` when the window has no area in the polarity direction."""
    sl = window_slice(times_ms, window_ms)
    sign = 1.0 if polarity == "positive" else -1.0
    a = np.clip(sign * np.asarray(data_uv[sl], dtype=float), 0.0, None)
    dt = float(np.median(np.diff(times_ms))) / 1000 if times_ms.size > 1 else 1.0
    total = np.float32(dt * _trapz(a))
    if total == 0:
        return float("nan")
    target = np.float32(total * fraction)
    lo, hi, mid = 0, a.size - 1, 0
    while lo <= hi:
        mid = int(np.floor((lo + hi) / 2 + 0.5))  # MATLAB round
        area = np.float32(dt * _trapz(a[: mid + 1]))
        if area > target:
            hi = mid - 1
        elif area < target:
            lo = mid + 1
        else:
            break
    return float(times_ms[sl][mid])


def pool(data_uv: np.ndarray) -> np.ndarray:
    """Mean waveform of (n_channels, n_times) (an ROI pooled before measurement)."""
    return np.asarray(data_uv, dtype=float).mean(axis=0)


def sme_mean_amplitude(
    times_ms: np.ndarray, trials_uv: np.ndarray, window_ms: tuple[float, float]
) -> float:
    """Analytic standardized measurement error of the mean amplitude (Luck et al. 2021):
    SD of the single-trial mean amplitudes / sqrt(n). ``trials_uv``: (n_trials, n_times)."""
    n = trials_uv.shape[0]
    if n < 2:
        return float("nan")
    single = trials_uv[:, window_slice(times_ms, window_ms)].mean(axis=1)
    return float(single.std(ddof=1) / np.sqrt(n))
