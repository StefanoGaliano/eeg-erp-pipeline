"""Pipeline features the erpcore-exact recipe needs (BLUEPRINT 8.2a), on synthetic data.

- reference over listed channels incl. EOG electrodes, applied to EOG too, bads allowed;
- precomputed (EEGLAB) ICA: removing a component is exact on a known mixture;
- ERPLAB-style rejection rules with test periods (window2sample) and inclusive extval;
- peak measures on a low-passed average.
"""

from __future__ import annotations

import mne
import numpy as np
import pytest
import scipy.io

from eegunify.config.erp_sections import (
    ArtifactsSection,
    PrecomputedIcaSpec,
    PreprocSection,
    RejectRule,
    RejectSpec,
)
from eegunify.config.schema import ReferenceSection
from eegunify.erp.epochs import EpochsError, erplab_window, rules_rejection
from eegunify.erp.ica import IcaError
from eegunify.erp.precomputed_ica import apply_precomputed, read_eeglab_decomposition
from eegunify.erp.preprocess import PreprocError, rereference

SF = 256.0


def _raw(names: list[str], types: list[str], data: np.ndarray) -> mne.io.RawArray:
    return mne.io.RawArray(data, mne.create_info(names, SF, types), verbose=False)


# --- reference ---------------------------------------------------------------------------------


def test_reference_over_eeg_and_eog_applied_to_eog_not_to_bipolars() -> None:
    rng = np.random.default_rng(0)
    names = ["Fp1", "PO8", "HEOG_left", "HEOG_right", "HEOG_uncorr"]
    types = ["eeg", "eeg", "eog", "eog", "eog"]
    data = rng.normal(size=(5, 100))
    data[4] = data[2] - data[3]  # a derived bipolar
    raw = _raw(names, types, data.copy())
    raw.info["bads"] = ["Fp1"]
    ref = ReferenceSection(
        scheme="channels",
        channels=["Fp1", "PO8", "HEOG_left", "HEOG_right"],
        apply_to_eog=True,
        allow_bad_channels=True,
    )
    steps = rereference(raw, ref, reference_free=True, derived=["HEOG_uncorr"])
    mean = data[:4].mean(axis=0)
    np.testing.assert_allclose(raw.get_data()[:4], data[:4] - mean)
    np.testing.assert_allclose(raw.get_data()[4], data[4])  # bipolar untouched
    assert steps["bad_in_reference"] == ["Fp1"]
    assert "HEOG_uncorr" not in steps["applied_to"]


def test_reference_without_apply_to_eog_leaves_eog() -> None:
    data = np.arange(300, dtype=float).reshape(3, 100)
    raw = _raw(["Cz", "Pz", "VEOG_lower"], ["eeg", "eeg", "eog"], data.copy())
    rereference(
        raw, ReferenceSection(scheme="channels", channels=["Cz", "VEOG_lower"]), reference_free=True
    )
    np.testing.assert_allclose(raw.get_data()[2], data[2])
    np.testing.assert_allclose(raw.get_data()[1], data[1] - (data[0] + data[2]) / 2)


def test_bad_reference_channel_refused_unless_allowed() -> None:
    raw = _raw(["Cz", "Pz"], ["eeg", "eeg"], np.ones((2, 10)))
    raw.info["bads"] = ["Cz"]
    with pytest.raises(PreprocError, match="are bad"):
        rereference(raw, ReferenceSection(scheme="channels", channels=["Cz"]), reference_free=True)


def test_reference_flags_need_channels_scheme() -> None:
    with pytest.raises(ValueError, match="only used with"):
        ReferenceSection(scheme="average", apply_to_eog=True)


# --- precomputed ICA ---------------------------------------------------------------------------


def _write_set(path, labels: list[str], weights: np.ndarray, sphere: np.ndarray, chanind) -> None:
    chanlocs = np.zeros((len(labels),), dtype=[("labels", object)])
    for i, lab in enumerate(labels):
        chanlocs[i]["labels"] = lab
    eeg = {
        "icaweights": weights,
        "icasphere": sphere,
        "icachansind": np.asarray(chanind, dtype=float),
        "chanlocs": chanlocs,
        "nbchan": float(len(labels)),
    }
    scipy.io.savemat(path, {"EEG": eeg})


def test_precomputed_ica_removes_a_known_source_exactly(tmp_path) -> None:
    rng = np.random.default_rng(1)
    sources = rng.normal(size=(3, 500))
    mixing = rng.normal(size=(3, 3))
    unmix = np.linalg.inv(mixing)
    # EEGLAB: unmixing = weights @ sphere; split it arbitrarily into two factors
    sphere = rng.normal(size=(3, 3))
    weights = unmix @ np.linalg.inv(sphere)
    # The .set lists 4 channels; ICA used channels 1, 2, 4 (1-based), labels in capitals.
    path = tmp_path / "x.set"
    _write_set(path, ["FP1", "PO8", "X", "VEOG_LOWER"], weights, sphere, [1, 2, 4])
    decomp = read_eeglab_decomposition(path)
    assert decomp.channels == ["FP1", "PO8", "VEOG_LOWER"]
    data = np.vstack([mixing @ sources, rng.normal(size=(1, 500))])
    raw = _raw(["Fp1", "PO8", "VEOG_lower", "Oz"], ["eeg", "eeg", "eog", "eeg"], data.copy())
    decision = apply_precomputed(raw, decomp, [2])  # remove the 2nd source (EEGLAB numbering)
    expected = mixing[:, [0, 2]] @ sources[[0, 2]]
    np.testing.assert_allclose(raw.get_data()[:3], expected, atol=1e-10)
    np.testing.assert_allclose(raw.get_data()[3], data[3])  # not in the decomposition
    assert decision.excluded == [1]
    assert list(decision.table["eeglab_component"]) == [1, 2, 3]


def test_precomputed_ica_refuses_bad_numbers_and_missing_channels(tmp_path) -> None:
    path = tmp_path / "x.set"
    _write_set(path, ["A", "B"], np.eye(2), np.eye(2), [1, 2])
    decomp = read_eeglab_decomposition(path)
    raw = _raw(["A", "B"], ["eeg", "eeg"], np.ones((2, 10)))
    with pytest.raises(IcaError, match="only 2 components"):
        apply_precomputed(raw, decomp, [3])
    with pytest.raises(IcaError, match="not in the data"):
        apply_precomputed(_raw(["A", "C"], ["eeg", "eeg"], np.ones((2, 10))), decomp, [1])


def test_precomputed_config_validation() -> None:
    with pytest.raises(ValueError, match=r"requires artifacts\.precomputed"):
        ArtifactsSection(method="precomputed")
    with pytest.raises(ValueError, match="start at 1"):
        PrecomputedIcaSpec(weights="x.set", exclude_eeglab=[0])
    with pytest.raises(ValueError, match="both define"):
        ArtifactsSection(eog_bipolar={"HEOG": ("a", "b")}, corrected_bipolar={"HEOG": ("a", "b")})
    assert PreprocSection(interpolate_after_ica=True).interpolate_after_ica


# --- rejection rules ---------------------------------------------------------------------------


def _epochs(data_uv: np.ndarray, names: list[str], tmin: float = -0.2) -> mne.EpochsArray:
    info = mne.create_info(names, SF, ["eeg"] * len(names))
    return mne.EpochsArray(data_uv * 1e-6, info, tmin=tmin, verbose=False)


def test_erplab_window_matches_window2sample() -> None:
    times = np.arange(-51, 205) / SF  # ERP CORE epochs at 256 Hz: -199.2 .. 796.9 ms
    # p = round(t*fs) + round(|xmin|*fs) (1-based in ERPLAB, minus 1 here)
    assert erplab_window(times, (-25.0, 325.0), SF) == (-6 + 51, 83 + 51)
    assert erplab_window(times, (-200.0, 800.0), SF) == (0, 255)  # clipped like ERPLAB
    assert erplab_window(times, None, SF) == (0, 255)
    with pytest.raises(EpochsError, match="outside the epoch"):
        erplab_window(times, (-500.0, 800.0), SF)


def test_extval_is_inclusive_and_respects_the_window() -> None:
    x = np.zeros((4, 1, 256))
    x[0, 0, 100] = 200.0  # exactly at the bound: flagged (ERPLAB >=)
    x[1, 0, 100] = 199.9  # inside the bounds: kept
    x[2, 0, 0] = -300.0  # outside the test period below: kept
    x[3, 0, 60] = -200.0  # lower bound, inside the period: flagged
    rule = RejectRule(
        test="extval", channels=["oz"], min_uv=-200, max_uv=200, window_ms=(0.0, 600.0)
    )
    rej = rules_rejection(_epochs(x, ["Oz"]), RejectSpec(method="rules", rules=[rule]))
    assert [bool(r) for r in rej.reasons] == [True, False, False, True]
    assert "rule 1 extval" in rej.reasons[0]


def test_ptp_and_step_rules_in_a_test_period() -> None:
    x = np.zeros((3, 2, 256))
    x[0, 1, 60:70] = 160.0  # 160 uV blink-like on VEOG inside -25..325 ms
    x[1, 1, 200:210] = 160.0  # same, but after 325 ms: outside the period
    x[2, 0, 100:] = 40.0  # 40 uV step on HEOG
    rules = [
        RejectRule(
            test="ptp",
            channels=["VEOG"],
            threshold_uv=150,
            window_ms=(-25, 325),
            win_ms=175,
            step_ms=10,
        ),
        RejectRule(test="step", channels=["HEOG"], threshold_uv=32, win_ms=100, step_ms=10),
    ]
    rej = rules_rejection(_epochs(x, ["HEOG", "VEOG"]), RejectSpec(method="rules", rules=rules))
    assert [r.split(" ")[:2] for r in rej.reasons if r] == [["rule", "1"], ["rule", "2"]]
    assert not rej.reasons[1]
    assert rej.reasons[2].startswith("rule 2 step")


def test_rule_validation_and_unknown_channels() -> None:
    with pytest.raises(ValueError, match="needs min_uv"):
        RejectRule(test="extval", channels=["Oz"])
    with pytest.raises(ValueError, match="needs threshold_uv"):
        RejectRule(test="ptp", channels=["Oz"])
    rule = RejectRule(test="ptp", channels=["Nope"], threshold_uv=100)
    with pytest.raises(EpochsError, match="not in the epochs"):
        rules_rejection(
            _epochs(np.zeros((1, 1, 256)), ["Oz"]), RejectSpec(method="rules", rules=[rule])
        )
