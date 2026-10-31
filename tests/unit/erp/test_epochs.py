"""epochs stage (M-E3): exact synthetic rejections (BLUEPRINT_PHASES 8.1), per-epoch log, F1."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.config.erp_sections import EpochSet, RejectSpec
from eegunify.config.loader import parse_config
from eegunify.config.schema import Config
from eegunify.erp import paths, qa
from eegunify.erp.epochs import (
    EpochsError,
    make_epochs,
    moving_window_ptp,
    ms_to_samples,
    step_like,
    trial_type,
)
from eegunify.erp.runner import Runner
from tests.synthetic.erp import ErpTruth, erp_config, make_erp_recording
from tests.unit.erp.conftest import _convert

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")
Make = Callable[..., tuple[Path, Config]]
STIM = EpochSet(lock="stimulus/*")
ERP_SECTIONS = {
    "epochs": {
        "sets": {"stim": {"lock": "stimulus/*"}},
        "reject": {"min_trials_per_condition": 10},
    },
    "conditions": {"face": ["stimulus/face"], "car": ["stimulus/car"]},
    "contrasts": {"face_minus_car": {"plus": "face", "minus": "car"}},
}

# --- the rules on hand-made arrays ----------------------------------------------------------


def test_moving_window_ptp() -> None:
    x = np.zeros((2, 1, 10))
    x[0, 0, 4] = 5.0  # inside windows starting at 2 and 4
    x[1, 0, :5], x[1, 0, 5:] = 0.0, 3.0  # a step: ptp 3 in the windows spanning it
    np.testing.assert_allclose(moving_window_ptp(x, 4, 2)[:, 0], [5.0, 3.0])
    # A drift spread over the epoch: no 4-sample window sees more than 3 steps of it.
    ramp = np.arange(10.0)[None, None]
    assert moving_window_ptp(ramp, 4, 2)[0, 0] == 3.0


def test_step_like() -> None:
    x = np.zeros((1, 1, 12))
    x[0, 0, 6:] = 10.0
    # Window 4 at 4: halves [4,5]=0 and [6,7]=10 -> 10.
    assert step_like(x, 4, 2)[0, 0] == 10.0
    noise = np.random.default_rng(0).standard_normal((1, 1, 1000))
    assert step_like(noise, 100, 10)[0, 0] < 0.6


def test_ms_to_samples_rounds_half_up() -> None:
    assert ms_to_samples(10, 250) == 3  # 2.5 -> 3 (Python round() would give 2)
    assert ms_to_samples(200, 256) == 51 and ms_to_samples(0.1, 256) == 1


def test_trial_type() -> None:
    assert trial_type("stimulus/face/12") == "stimulus/face"
    assert trial_type("stimulus/face") == "stimulus/face"
    assert trial_type("response/correct") == "response/correct"


# --- synthetic recording with injected bad epochs -------------------------------------------

BAD_PTP, BAD_STEP = (3, 10, 21), (5, 30)


@pytest.fixture(scope="module")
def clean_truth() -> ErpTruth:
    return make_erp_recording(60.0, dead=False, noisy=False, line_uV=0.0, seed=1)


def _injected(truth: ErpTruth) -> mne.io.RawArray:
    data = truth.data_uV.copy()
    sf = truth.sfreq
    t = np.arange(int(0.05 * sf)) / sf
    spike = 150.0 * np.exp(-(((t - 0.025) / 0.005) ** 2))
    oz, heog = truth.index("Oz"), truth.index("HEOG")
    for i in BAD_PTP:
        s = truth.samples[i] + int(0.3 * sf)
        data[oz, s : s + spike.size] += spike
    for i in BAD_STEP:
        s = truth.samples[i]
        data[heog, s + int(0.2 * sf) : s + int(0.5 * sf)] += 60.0
    raw = mne.io.RawArray(data * 1e-6, mne.create_info(truth.names, sf, truth.types), verbose=False)
    raw.set_annotations(
        mne.Annotations(truth.samples / sf, 0.0, [f"stimulus/{c}" for c in truth.conditions])
    )
    return raw


def test_injected_epochs_rejected_exactly(clean_truth: ErpTruth) -> None:
    res = make_epochs(_injected(clean_truth), "stim", STIM, RejectSpec())
    log = res.log
    assert len(log) == len(clean_truth.samples)
    rejected = set(log.loc[~log["kept"], "idx"])
    assert rejected == set(BAD_PTP) | set(BAD_STEP)
    for i in BAD_PTP:
        assert log.loc[i, "reason"].startswith("ptp") and "Oz" in log.loc[i, "reason"]
    for i in BAD_STEP:
        assert log.loc[i, "reason"].startswith("step") and log.loc[i, "step_uv"] > 32
    assert len(res.epochs) == len(log) - len(rejected)
    # Counts per condition are exact.
    kept = log[log["kept"]]
    truth_kept = [c for i, c in enumerate(clean_truth.conditions) if i not in rejected]
    assert (kept["trial_type"] == "stimulus/face").sum() == truth_kept.count("face")
    assert list(res.epochs.events[:, 0]) == [
        s for i, s in enumerate(clean_truth.samples) if i not in rejected
    ]


def test_baseline_applied_and_window(clean_truth: ErpTruth) -> None:
    res = make_epochs(_injected(clean_truth), "stim", STIM, RejectSpec())
    assert res.epochs.tmin == pytest.approx(-0.2) and res.epochs.tmax == pytest.approx(0.8)
    base = res.epochs.copy().crop(None, 0).get_data(picks="eeg").mean(axis=-1)
    assert np.abs(base).max() < 1e-12


def test_flat_and_none(clean_truth: ErpTruth) -> None:
    raw = _injected(clean_truth)
    flat = make_epochs(raw, "stim", STIM, RejectSpec(ptp_uv=None, step_heog_uv=None, flat_uv=1e4))
    assert not flat.log["kept"].any() and flat.log["reason"].str.startswith("flat").all()
    none = make_epochs(raw, "stim", STIM, RejectSpec(method="none"))
    assert none.log["kept"].all()


def test_epoch_past_data_end_logged(clean_truth: ErpTruth) -> None:
    raw = _injected(clean_truth).crop(tmax=clean_truth.samples[-1] / clean_truth.sfreq + 0.3)
    log = make_epochs(raw, "stim", STIM, RejectSpec()).log
    assert not log["kept"].iloc[-1] and log["reason"].iloc[-1] == "TOO_SHORT"


def test_lock_matching_nothing_refused(clean_truth: ErpTruth) -> None:
    with pytest.raises(EpochsError, match="matches no event"):
        make_epochs(_injected(clean_truth), "x", EpochSet(lock="response/*"), RejectSpec())


def test_autoreject_without_package(clean_truth: ErpTruth, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "autoreject", None)
    with pytest.raises(EpochsError, match="autoreject"):
        make_epochs(_injected(clean_truth), "stim", STIM, RejectSpec(method="autoreject"))


def test_qa_rules() -> None:
    assert qa.rejected_fraction_check(10, 100, {}).level == "pass"
    assert qa.rejected_fraction_check(30, 100, {}).level == "warn"
    assert qa.rejected_fraction_check(51, 100, {}).level == "fail"
    assert qa.min_trials_check({"stim/face": 29}, 30).level == "fail"
    assert qa.min_trials_check({"stim/face": 30}, 30).level == "pass"
    assert qa.baseline_drift_check({"stim/face": 2.5}).level == "warn"
    assert qa.baseline_drift_check({"stim/face": float("nan")}).level == "pass"


def test_reserved_set_name() -> None:
    with pytest.raises(ValueError, match="reserved"):
        parse_config(erp_config(epochs={"sets": {"epochs": {"lock": "stimulus/*"}}}))


# --- stage ----------------------------------------------------------------------------------


def _out(root: Path, cfg: Config, desc: str, suffix: str, ext: str) -> Path:
    return paths.stage_path(
        root, "epochs", cfg, "01", description=desc, suffix=suffix, extension=ext
    )


def test_stage_after_ica_and_cache(blink_project: Make, blink_truth: ErpTruth) -> None:
    root, cfg = blink_project(**ERP_SECTIONS)
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["epochs"].status == "ok", outs["epochs"].message
    log = pd.read_csv(_out(root, cfg, "stim", "log", ".tsv"), sep="\t")
    assert list(log.columns) == [
        "idx",
        "onset_s",
        "label",
        "trial_type",
        "code",
        "kept",
        "reason",
        "ptp_uv",
        "step_uv",
    ]
    assert len(log) == len(blink_truth.samples)
    assert list(log["code"]) == list(blink_truth.codes)
    epochs = mne.read_epochs(_out(root, cfg, "stim", "epo", ".fif"), verbose=False)
    assert len(epochs) == log["kept"].sum()
    data = json.loads(_out(root, cfg, "epochs", "qa", ".json").read_text(encoding="utf-8"))
    assert data["input"].endswith("_desc-clean_eeg.fif")
    assert set(data["kept_per_condition"]) == {"stim/face", "stim/car"}
    # Changing the rejection threshold reruns only epochs.
    sections = {
        **ERP_SECTIONS,
        "epochs": {
            **ERP_SECTIONS["epochs"],
            "reject": {"ptp_uv": 80, "min_trials_per_condition": 10},
        },
    }
    st = {it.stage: it.status for it in Runner(parse_config(erp_config(**sections)), root).status()}
    assert st == {"preproc": "ok", "ica": "ok", "epochs": "stale"}


def test_stage_reads_preproc_without_ica(blink_project: Make) -> None:
    root, cfg = blink_project(artifacts={"method": "none"}, **ERP_SECTIONS)
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert set(outs) == {"preproc", "epochs"}
    data = json.loads(_out(root, cfg, "epochs", "qa", ".json").read_text(encoding="utf-8"))
    assert data["input"].endswith("_desc-preproc_eeg.fif")


def test_stage_min_trials_fails_without_epo(blink_project: Make) -> None:
    sections = {**ERP_SECTIONS, "epochs": {"sets": {"stim": {"lock": "stimulus/*"}}}}
    root, cfg = blink_project(artifacts={"method": "none"}, **sections)  # default minimum 30
    out = {o.item.stage: o for o in Runner(cfg, root).run()}["epochs"]
    assert out.status == "failed" and "fewer than 30" in out.message
    assert _out(root, cfg, "stim", "log", ".tsv").exists()
    assert not _out(root, cfg, "stim", "epo", ".fif").exists()


def test_no_sets_disables_stage(blink_project: Make) -> None:
    root, cfg = blink_project(artifacts={"method": "none"})
    assert [it.stage for it in Runner(cfg, root).status()] == ["preproc"]


def test_f1_trial_type_with_several_codes(tmp_path: Path, erp_truth: ErpTruth) -> None:
    """F1: a trial type with several codes gets labels ``stimulus/face/<code>``; the
    ``stimulus/face`` condition must still select every one of them."""
    cfg_d = erp_config(
        events={"mapping": {"1": "stimulus/face", "2": "stimulus/face"}, "unmapped": "error"},
        artifacts={"method": "none"},
        epochs={
            "sets": {"stim": {"lock": "stimulus/face"}},
            "reject": {"method": "none", "min_trials_per_condition": 1},
        },
        conditions={"face": ["stimulus/face"]},
    )
    _convert(tmp_path, erp_truth, cfg_d)
    cfg = parse_config(cfg_d)
    out = {o.item.stage: o for o in Runner(cfg, tmp_path).run()}["epochs"]
    assert out.status == "ok", out.message
    log = pd.read_csv(_out(tmp_path, cfg, "stim", "log", ".tsv"), sep="\t")
    assert set(log["label"]) == {"stimulus/face/1", "stimulus/face/2"}
    assert len(log) == len(erp_truth.samples)
    assert (log["trial_type"] == "stimulus/face").all()
    assert out.result is not None
    assert out.result.extra["kept_per_condition"] == {"stim/face": len(erp_truth.samples)}
