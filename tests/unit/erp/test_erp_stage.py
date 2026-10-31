"""erp stage (M-E4): averages, contrasts, measures.tsv, QA and figures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.channels.montage import _standard
from eegunify.config.loader import parse_config
from eegunify.config.schema import Config
from eegunify.erp import paths, qa
from eegunify.erp.averaging import (
    MEASURES_COLUMNS,
    ErpError,
    add_contrasts,
    average_conditions,
    measure_component,
)
from eegunify.erp.components.library import resolve
from eegunify.erp.measure import mean_amplitude
from eegunify.erp.recipes import apply_recipe
from eegunify.erp.runner import Runner
from tests.synthetic.erp import AMPLITUDE_UV, N170_MS, N170_SIGMA_MS, ErpTruth, erp_config

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")
Make = Callable[..., tuple[Path, Config]]
SECTIONS: dict[str, Any] = {
    "epochs": {
        "sets": {"stim": {"lock": "stimulus/*"}},
        "reject": {"min_trials_per_condition": 10},
    },
    "conditions": {"face": ["stimulus/face"], "car": ["stimulus/car"]},
    "contrasts": {"face_minus_car": {"plus": "face", "minus": "car"}},
    "components": {"preset": "erpcore", "use": ["n170"]},
}


def _cfg(**extra: Any) -> Config:
    return parse_config(erp_config(**{**SECTIONS, **extra}))


def _erp_only(truth: ErpTruth) -> np.ndarray:
    """The injected N170 alone (µV), as make_erp_recording builds it."""
    out = np.zeros_like(truth.data_uV)
    t = np.arange(int(0.4 * truth.sfreq)) / truth.sfreq * 1000
    wave = np.exp(-(((t - N170_MS) / N170_SIGMA_MS) ** 2) / 2)
    for s, c in zip(truth.samples, truth.conditions, strict=True):
        out[:, s : s + wave.size] += AMPLITUDE_UV[c] * np.outer(truth.topography, wave)
    return out


def _epochs(truth: ErpTruth, data_uv: np.ndarray) -> mne.Epochs:
    raw = mne.io.RawArray(
        data_uv * 1e-6, mne.create_info(truth.names, truth.sfreq, truth.types), verbose=False
    )
    raw.set_montage(_standard("standard_1020"), verbose=False)
    raw.set_annotations(
        mne.Annotations(
            truth.samples / truth.sfreq, 0.0, [f"stimulus/{c}" for c in truth.conditions]
        )
    )
    events, ids = mne.events_from_annotations(raw, verbose=False)
    return mne.Epochs(raw, events, ids, -0.2, 0.8, baseline=None, preload=True, verbose=False)


def _truth_difference(truth: ErpTruth, times_ms: np.ndarray, weight: float) -> np.ndarray:
    gauss = np.exp(-(((times_ms - N170_MS) / N170_SIGMA_MS) ** 2) / 2)
    gauss[times_ms < 0] = 0.0
    return (AMPLITUDE_UV["face"] - AMPLITUDE_UV["car"]) * weight * gauss


def test_noiseless_difference_wave_exact(erp_truth: ErpTruth) -> None:
    cfg = _cfg()
    epochs = _epochs(erp_truth, _erp_only(erp_truth))
    evokeds = add_contrasts(average_conditions(epochs, cfg.conditions), cfg)
    assert set(evokeds) == {"face", "car", "face_minus_car"}
    assert evokeds["face_minus_car"].comment == "face_minus_car"
    times = evokeds["face"].times * 1000
    po8 = evokeds["face_minus_car"].copy().pick(["PO8"]).data[0] * 1e6
    expected = _truth_difference(erp_truth, times, erp_truth.topography[erp_truth.index("PO8")])
    np.testing.assert_allclose(po8, expected, atol=1e-6)
    [rc] = resolve(cfg.components).components.values()
    cm = measure_component(rc, evokeds, epochs, cfg, subject="01", stage_key="k")
    assert cm.mean_uv == pytest.approx(mean_amplitude(times, expected, (110.0, 150.0)), abs=1e-6)
    assert cm.sme_uv == pytest.approx(0.0, abs=1e-9)  # every trial identical
    rows = pd.DataFrame(cm.rows, columns=MEASURES_COLUMNS)
    lat = rows.query("wave == 'face_minus_car' and roi == 'primary' and measure == 'peak_latency'")
    assert lat["value"].iloc[0] == pytest.approx(N170_MS, abs=1000 / erp_truth.sfreq)
    ntr = rows.query("wave == 'face_minus_car'")["n_trials"].iloc[0]
    assert ntr == min(erp_truth.conditions.count("face"), erp_truth.conditions.count("car"))
    assert set(rows["roi"]) == {"primary", "secondary"} and set(rows["channels"]) == {
        "PO8",
        "P7",
        "PO7",
        "P8",
    }


def test_unknown_roi_and_lateralized_refused(erp_truth: ErpTruth) -> None:
    epochs = _epochs(erp_truth, _erp_only(erp_truth))
    for override, match in (
        ({"rois": {"primary": ["PO10"]}}, "not in the data"),
        ({"lateralized_pairs": [["PO7", "PO8"]]}, "lateralised"),
    ):
        cfg = _cfg(
            components={"preset": "erpcore", "use": ["n170"], "overrides": {"n170": override}}
        )
        evokeds = add_contrasts(average_conditions(epochs, cfg.conditions), cfg)
        [rc] = resolve(cfg.components).components.values()
        with pytest.raises(ErpError, match=match):
            measure_component(rc, evokeds, epochs, cfg, subject="01", stage_key="k")


def test_erp_qa_rules() -> None:
    checks = {
        c.id: c
        for c in qa.erp_checks(
            {"n170": 2.5}, {"n170": (1.0, "negative")}, {"n170": ["car@primary:PO8"]}, 120.0
        )
    }
    assert checks["ERP_NOISE_SME"].level == "warn"
    assert checks["ERP_COMPONENT_POLARITY"].level == "warn"
    assert checks["MEASURE_NO_LOCAL_PEAK"].level == "warn"
    vep = checks["VEP_LATENCY_ATYPICAL"]
    assert vep.level == "pass" and "atypically late" in vep.message
    ok = {
        c.id: c.level
        for c in qa.erp_checks({"n170": 0.5}, {"n170": (-1.0, "negative")}, {"n170": []})
    }
    assert ok == {
        "ERP_NOISE_SME": "pass",
        "ERP_COMPONENT_POLARITY": "pass",
        "MEASURE_NO_LOCAL_PEAK": "pass",
    }


def _out(root: Path, cfg: Config, desc: str, suffix: str, ext: str) -> Path:
    return paths.stage_path(root, "erp", cfg, "01", description=desc, suffix=suffix, extension=ext)


def test_stage_end_to_end(blink_project: Make, blink_truth: ErpTruth) -> None:
    root, cfg = blink_project(**SECTIONS)
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["erp"].status == "ok", outs["erp"].message
    tsv = pd.read_csv(_out(root, cfg, "stim", "measures", ".tsv"), sep="\t")
    assert list(tsv.columns) == MEASURES_COLUMNS
    assert (tsv["stage_key"] == outs["erp"].item.key).all()
    evs = mne.read_evokeds(_out(root, cfg, "stim", "ave", ".fif"), verbose=False)
    assert [e.comment for e in evs] == ["face", "car", "face_minus_car"]
    assert _out(root, cfg, "stimN170", "erp", ".png").exists()
    qa_json = json.loads(_out(root, cfg, "erp", "qa", ".json").read_text(encoding="utf-8"))
    sme = qa_json["checks"][0]["value"]["n170"]
    # Measured vs injected: the average reference subtracts the topography's mean.
    eeg = [i for i, t in enumerate(blink_truth.types) if t == "eeg"]
    weight = blink_truth.topography[blink_truth.index("PO8")] - blink_truth.topography[eeg].mean()
    times = evs[0].times * 1000
    truth = mean_amplitude(times, _truth_difference(blink_truth, times, weight), (110.0, 150.0))
    row = tsv.query("wave == 'face_minus_car' and roi == 'primary' and measure == 'mean_amplitude'")
    assert abs(row["value"].iloc[0] - truth) < 3 * sme + 0.2
    assert qa_json["n_trials"]["stim"].keys() == {"face", "car"}
    # The figure low-pass only reruns erp.
    cfg2 = _cfg(erp={"plot_lowpass_hz": 30})
    st = {it.stage: it.status for it in Runner(cfg2, root).status()}
    assert st == {"preproc": "ok", "ica": "ok", "epochs": "ok", "erp": "stale"}


def test_stage_without_components_or_figures(blink_project: Make) -> None:
    root, cfg = blink_project(
        artifacts={"method": "none"},
        erp={"figures": False},
        **{k: v for k, v in SECTIONS.items() if k != "components"},
    )
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["erp"].status == "ok", outs["erp"].message
    assert pd.read_csv(_out(root, cfg, "stim", "measures", ".tsv"), sep="\t").empty
    assert not list(_out(root, cfg, "stim", "ave", ".fif").parent.glob("*.png"))


def test_preview_recipe_patch() -> None:
    cfg = apply_recipe(_cfg(), "preview")
    assert cfg.artifacts.method == "none" and cfg.preproc.lowpass_hz == 30
    assert cfg.preproc.filter.method == "fir" and not cfg.preproc.interpolate
    assert cfg.epochs.reject.ptp_uv == 150 and cfg.epochs.reject.step_heog_uv is None
    assert cfg.epochs.reject.min_trials_per_condition == 10  # kept from the dataset config
    assert cfg.reference.online == "Cz" and cfg.conditions == _cfg().conditions
