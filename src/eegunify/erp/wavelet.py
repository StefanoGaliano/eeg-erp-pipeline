"""Wavelet-thresholding artifact correction (``artifacts.method: wavelet``).

The approach of HAPPE's ERP mode (Gabard-Durnam et al. 2018, Front. Neurosci. 12:97; HAPPE+ER,
Monachino et al. 2022, Dev. Cogn. Neurosci. 57:101140): decompose each EEG channel with a
discrete wavelet transform, keep the coefficients a level-dependent threshold lets through,
and treat their reconstruction as the artifact (large, transient deflections such as blinks
and movement, plus the slowest drift in the approximation band); the cleaned signal is the
data minus that artifact.

Implemented here with PyWavelets, not a port of HAPPE's code (GPL-3). HAPPE calls MATLAB's
``wdenoise`` with an empirical-Bayes threshold (``DenoisingMethod: Bayes``), which has no
Python equivalent. Two thresholds are offered, both with a level-dependent noise estimate
(median absolute deviation / 0.6745 per level):

* ``universal`` (default; Donoho & Johnstone 1994): sigma * sqrt(2 ln n). On HAPPE's GABA
  tutorial files the pre/post correlation was 0.64-0.77 (HAPPE's own QC: 0.67-0.91);
* ``bayesshrink`` (Chang, Yu & Vetterli 2000, IEEE Trans. Image Process. 9:1532): adapts to
  each level's signal, removes synthetic blinks almost fully (96 % vs 76 %) but on infant EEG
  kept only 2-15 % of the variance (pre/post r 0.42-0.57), the VEP included.

Results are close to HAPPE's in spirit, not identical.
"""

from __future__ import annotations

from typing import Any

import mne
import numpy as np

from eegunify.config.erp_sections import WaveletSpec


def happe_level(sfreq: float) -> int:
    """HAPPE's ERP decomposition level for a sampling rate."""
    if sfreq > 500:
        return 11
    if sfreq > 250:
        return 10
    return 9


def bayes_shrink(detail: np.ndarray) -> float:
    """BayesShrink threshold of one level: sigma_noise^2 / sigma_signal (MAD noise estimate);
    the largest coefficient (everything removed) when the level is pure noise."""
    sigma_n = float(np.median(np.abs(detail)) / 0.6745)
    sigma_x2 = max(float(np.mean(detail**2)) - sigma_n**2, 0.0)
    if sigma_x2 == 0.0:
        return float(np.max(np.abs(detail))) if detail.size else 0.0
    return float(sigma_n**2 / np.sqrt(sigma_x2))


def universal(detail: np.ndarray, n: int) -> float:
    """Universal threshold sigma * sqrt(2 ln n), sigma from the level's MAD."""
    sigma = float(np.median(np.abs(detail)) / 0.6745)
    return sigma * float(np.sqrt(2 * np.log(n)))


def artifact(x: np.ndarray, spec: WaveletSpec, sfreq: float) -> tuple[np.ndarray, int]:
    """The wavelet-thresholded part of one channel (what gets subtracted) and the level used."""
    import pywt

    wav = pywt.Wavelet(spec.wavelet)
    level = spec.level or happe_level(sfreq)
    level = max(1, min(level, pywt.dwt_max_level(x.size, wav.dec_len)))
    coeffs = pywt.wavedec(x, wav, level=level, mode="symmetric")

    def thr(d: np.ndarray) -> float:
        return bayes_shrink(d) if spec.threshold == "bayesshrink" else universal(d, x.size)

    kept = [coeffs[0]] + [pywt.threshold(d, thr(d), mode=spec.threshold_rule) for d in coeffs[1:]]
    out: np.ndarray = pywt.waverec(kept, wav, mode="symmetric")[: x.size]
    return out, level


def wavelet_clean(raw: mne.io.BaseRaw, spec: WaveletSpec) -> dict[str, Any]:
    """Subtract the wavelet artifact from every EEG channel of ``raw`` (in place).

    Returns the steps record, with ``variance_kept_pct`` = variance after / before over the
    EEG channels (HAPPE's data-QC measure).
    """
    picks = mne.pick_types(raw.info, eeg=True, exclude=[])
    data = raw.get_data(picks=picks)
    before = float(np.var(data))
    cleaned = np.empty_like(data)
    level = 0
    for i, x in enumerate(data):
        art, level = artifact(x, spec, raw.info["sfreq"])
        cleaned[i] = x - art
    raw._data[picks] = cleaned
    after = float(np.var(cleaned))
    return {
        "wavelet": {
            "wavelet": spec.wavelet,
            "level": level,
            "threshold": f"{spec.threshold}, level-dependent noise",
            "threshold_rule": spec.threshold_rule,
            "channels": len(picks),
            "variance_kept_pct": round(100 * after / before, 2) if before > 0 else float("nan"),
        }
    }
