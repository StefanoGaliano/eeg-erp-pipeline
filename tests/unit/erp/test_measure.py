"""measure.py (M-E4): analytic cases (no noise) and property tests (BLUEPRINT_PHASES 8.1)."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eegunify.erp.measure import (
    MeasureError,
    fractional_area_latency,
    mean_amplitude,
    peak,
    pool,
    sme_mean_amplitude,
    window_slice,
)

SF = 500.0
T = np.arange(-100, 401) * 1000 / SF  # -200..800 ms, 2 ms steps


def _gauss(center_ms: float, sigma_ms: float, amp: float) -> np.ndarray:
    return amp * np.exp(-(((T - center_ms) / sigma_ms) ** 2) / 2)


def test_mean_amplitude_exact() -> None:
    x = 0.5 * T + 3.0  # linear: the mean over a symmetric sample window is the centre value
    assert mean_amplitude(T, x, (100.0, 200.0)) == pytest.approx(0.5 * 150 + 3.0, abs=1e-9)
    assert window_slice(T, (100.0, 200.0)) == slice(150, 201)  # inclusive, 51 samples
    assert mean_amplitude(T, np.full(T.size, -2.5), (110.0, 150.0)) == pytest.approx(
        -2.5, abs=1e-12
    )


def test_window_snaps_to_nearest_samples() -> None:
    assert window_slice(T, (100.9, 198.9)) == slice(150, 200)  # 101 -> 100 ms, 198.9 -> 198 ms


def test_window_outside_epoch() -> None:
    with pytest.raises(MeasureError, match="outside the epoch"):
        mean_amplitude(T, np.zeros(T.size), (700.0, 900.0))


def test_peak_latency_to_one_sample() -> None:
    x = _gauss(131.0, 15.0, -4.0) + _gauss(300.0, 30.0, 6.0)
    pk = peak(T, x, (100.0, 200.0), "negative")
    assert pk.local and abs(pk.latency_ms - 131.0) <= 1000 / SF
    assert pk.amplitude_uv == pytest.approx(-4.0, abs=0.01)
    assert peak(T, x, (200.0, 400.0), "positive").latency_ms == pytest.approx(300.0, abs=2)


def test_no_local_peak_falls_back() -> None:
    ramp = -0.01 * T  # monotonic in the window: no local peak; most negative at the end
    pk = peak(T, ramp, (100.0, 200.0), "negative")
    assert not pk.local and pk.latency_ms == pytest.approx(200.0)


def test_local_peak_needs_neighborhood() -> None:
    # A one-sample notch on a slope is above its immediate neighbours but not above the mean of
    # three samples on the rising side.
    x = 0.2 * T
    i = int(np.argmin(np.abs(T - 150)))
    x[i] += 0.5
    assert peak(T, x, (140.0, 160.0), "positive", neighborhood=1).local
    assert not peak(T, x, (140.0, 160.0), "positive", neighborhood=3).local


def test_fractional_area_latency_triangle_exact() -> None:
    apex = 150.0
    tri = np.clip(1 - np.abs(T - apex) / 40.0, 0, None) * -3.0
    assert fractional_area_latency(T, tri, (100.0, 200.0), "negative") == pytest.approx(
        apex, abs=1e-9
    )
    # Positive parts are ignored for a negative component.
    mixed = tri + np.where(T >= 190, 5.0, 0.0)  # beyond the triangle
    assert fractional_area_latency(T, mixed, (100.0, 200.0), "negative") == pytest.approx(apex)
    assert np.isnan(fractional_area_latency(T, -tri, (100.0, 200.0), "negative"))


def test_fractional_area_fraction() -> None:
    box = np.where((T >= 100) & (T <= 198), 1.0, 0.0)  # 50 samples of 1 in the window
    # Trapezoid area to sample m is m sampling periods (49 in total), so 25 % = 12.25: ERPLAB's
    # binary search (0..49) visits 25, 12, 19, 16, 14, 13 and stops on sample 13 -> 126 ms.
    assert fractional_area_latency(T, box, (100.0, 198.0), "positive", 0.25) == 126.0


def test_pool_and_sme() -> None:
    assert np.allclose(pool(np.array([[1.0, 2.0], [3.0, 4.0]])), [2.0, 3.0])
    rng = np.random.default_rng(0)
    offsets = rng.normal(0, 2.0, 400)
    trials = offsets[:, None] + np.zeros((400, T.size))
    expected = offsets.std(ddof=1) / np.sqrt(400)
    assert sme_mean_amplitude(T, trials, (100.0, 200.0)) == pytest.approx(expected)
    assert np.isnan(sme_mean_amplitude(T, trials[:1], (100.0, 200.0)))


windows = st.tuples(st.integers(-90, 350), st.integers(2, 40)).map(
    lambda w: (T[100 + w[0]], T[100 + w[0] + w[1]])
)


def _wave(seed: int) -> np.ndarray:
    """Smooth random waveform (filtered noise plus a random Gaussian), µV."""
    rng = np.random.default_rng(seed)
    noise = np.convolve(rng.standard_normal(T.size + 20), np.ones(21) / 21, mode="valid")
    return 5 * noise + _gauss(rng.uniform(0, 400), rng.uniform(5, 50), rng.uniform(-10, 10))


waves = st.integers(0, 2**32 - 1).map(_wave)


@settings(max_examples=60, deadline=None)
@given(x=waves, w=windows)
def test_sign_symmetry(x: np.ndarray, w: tuple[float, float]) -> None:
    assert mean_amplitude(T, -x, w) == pytest.approx(-mean_amplitude(T, x, w), abs=1e-9)
    a, b = peak(T, x, w, "positive"), peak(T, -x, w, "negative")
    assert a.latency_ms == b.latency_ms and a.amplitude_uv == pytest.approx(-b.amplitude_uv)
    fa = fractional_area_latency(T, x, w, "positive")
    fb = fractional_area_latency(T, -x, w, "negative")
    assert (np.isnan(fa) and np.isnan(fb)) or fa == pytest.approx(fb)


@settings(max_examples=60, deadline=None)
@given(x=waves, w=windows, k=st.integers(1, 20))
def test_shift_invariance(x: np.ndarray, w: tuple[float, float], k: int) -> None:
    """Shifting the waveform and the window by k samples shifts latencies by k samples."""
    if w[1] + k * 2 > T[-1]:
        return
    shifted = np.roll(x, k)
    shifted[:k] = x[0]
    w2 = (w[0] + 2 * k, w[1] + 2 * k)
    assert mean_amplitude(T, shifted, w2) == pytest.approx(mean_amplitude(T, x, w), abs=1e-9)
    fa = fractional_area_latency(T, x, w, "positive")
    fb = fractional_area_latency(T, shifted, w2, "positive")
    assert (np.isnan(fa) and np.isnan(fb)) or fb == pytest.approx(fa + 2 * k)


@settings(max_examples=30, deadline=None)
@given(x=waves, w=windows)
def test_latencies_inside_window(x: np.ndarray, w: tuple[float, float]) -> None:
    pk = peak(T, x, w, "negative")
    assert w[0] <= pk.latency_ms <= w[1]
    fa = fractional_area_latency(T, x, w, "negative")
    dt = 1000 / SF
    assert np.isnan(fa) or w[0] - dt / 2 <= fa <= w[1] + dt / 2
