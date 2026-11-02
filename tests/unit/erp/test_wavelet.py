"""Wavelet-thresholding artifact correction (``artifacts.method: wavelet``, erp/wavelet.py).

On the synthetic blink recording the step must remove most of the blink at Fp1 and keep the
N170 at PO8. Three seeds checked when written: ``universal`` blink residual 19-24 %, N170
within 0.03 µV; ``bayesshrink`` residual 4-5 %, N170 within 0.24 µV.
Thresholding is not linear, so the cleaned data are compared with the blink-free truth.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import mne
import numpy as np
import pytest

from eegunify.config.erp_sections import WaveletSpec
from eegunify.config.schema import Config
from eegunify.erp import paths
from eegunify.erp.runner import Runner
from eegunify.erp.wavelet import bayes_shrink, happe_level, universal, wavelet_clean
from tests.synthetic.erp import N170_MS, ErpTruth

Make = Callable[..., tuple[Path, Config]]


def _clean(truth: ErpTruth, threshold: str) -> tuple[np.ndarray, dict]:
    raw = mne.io.RawArray(
        truth.data_uV * 1e-6, mne.create_info(truth.names, truth.sfreq, truth.types), verbose=False
    )
    steps = wavelet_clean(raw, WaveletSpec(threshold=threshold))  # type: ignore[arg-type]
    return raw.get_data() * 1e6, steps


@pytest.mark.parametrize(
    ("threshold", "max_residual", "n170_tol_uv"),
    [("universal", 0.35, 0.2), ("bayesshrink", 0.10, 0.5)],
)
def test_blink_removed_and_n170_kept(
    blink_truth: ErpTruth, threshold: str, max_residual: float, n170_tol_uv: float
) -> None:
    assert blink_truth.blink_uV is not None
    clean, steps = _clean(blink_truth, threshold)
    truth = blink_truth.data_uV - blink_truth.blink_uV
    fp1, po8 = blink_truth.index("Fp1"), blink_truth.index("PO8")
    residual = ((clean[fp1] - truth[fp1]) ** 2).sum() / (blink_truth.blink_uV[fp1] ** 2).sum()
    assert residual < max_residual

    def face_average(x: np.ndarray) -> np.ndarray:
        pre, post = 50, 200  # samples at 500 Hz: -100..400 ms
        ep = np.array(
            [
                x[s - pre : s + post]
                for s, c in zip(blink_truth.samples, blink_truth.conditions, strict=True)
                if c == "face"
            ]
        )
        ep = ep - ep[:, :pre].mean(axis=1, keepdims=True)
        return ep.mean(axis=0)

    i = 50 + int(N170_MS / 1000 * blink_truth.sfreq)
    assert face_average(clean[po8])[i] == pytest.approx(
        face_average(truth[po8])[i], abs=n170_tol_uv
    )
    w = steps["wavelet"]
    assert w["level"] == 10 and w["wavelet"] == "coif4" and 0 < w["variance_kept_pct"] < 100
    veog = blink_truth.index("VEOG")  # EOG is not thresholded (only EEG channels)
    np.testing.assert_allclose(clean[veog], blink_truth.data_uV[veog], atol=1e-6)


def test_rules() -> None:
    assert [happe_level(f) for f in (1000, 500, 256, 250)] == [11, 10, 10, 9]
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(4096)
    signal = noise.copy()
    signal[::64] += 50.0  # sparse large coefficients
    assert bayes_shrink(signal) < bayes_shrink(noise)  # signal present: lower threshold
    assert bayes_shrink(np.zeros(8)) == 0.0
    assert universal(noise, noise.size) == pytest.approx(
        np.median(np.abs(noise)) / 0.6745 * np.sqrt(2 * np.log(noise.size))
    )


def test_stage_writes_clean_data(blink_project: Make) -> None:
    root, cfg = blink_project(artifacts={"method": "wavelet"})
    outs = {o.item.stage: o for o in Runner(cfg, root).run(to="ica")}
    assert outs["ica"].status == "ok", outs["ica"].message
    assert outs["ica"].result is not None
    names = {p.name for p in outs["ica"].result.outputs}
    assert any(n.endswith("_desc-clean_eeg.fif") for n in names)
    assert not any(n.endswith("_components.tsv") for n in names)  # no ICA here
    qa_file = paths.stage_path(
        root, "ica", cfg, "01", description="ica", suffix="qa", extension=".json"
    )
    qa = json.loads(qa_file.read_text(encoding="utf-8"))
    [check] = qa["checks"]
    assert check["id"] == "WAVELET_VARIANCE_KEPT" and check["level"] == "pass"
    assert qa["steps"]["wavelet"]["threshold_rule"] == "soft"
