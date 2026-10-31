"""preproc stage (M-E1): synthetic ground truth (BLUEPRINT_PHASES 8.1) and stage outputs."""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from pathlib import Path

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.channels.montage import _standard
from eegunify.config.erp_sections import BadChannelsSpec, LineNoiseSpec, PreprocSection
from eegunify.config.loader import parse_config
from eegunify.config.schema import Config, ReferenceSection
from eegunify.erp import paths
from eegunify.erp.preprocess import (
    PreprocError,
    apply_filter,
    detect_bads,
    harmonics,
    interpolate,
    is_reference_free,
    preprocess,
    remove_line_noise,
    rereference,
)
from eegunify.erp.qa import line_peak_db
from eegunify.erp.runner import Runner
from tests.synthetic.erp import ErpTruth, erp_config

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")
Make = Callable[..., tuple[Path, Config]]


def _raw(truth: ErpTruth, montage: bool = True) -> mne.io.RawArray:
    raw = mne.io.RawArray(
        truth.data_uV * 1e-6, mne.create_info(truth.names, truth.sfreq, truth.types), verbose=False
    )
    if montage:
        raw.set_montage(_standard("standard_1020"), verbose=False)
    raw.info["line_freq"] = 50.0
    return raw


def _cfg(**sections: dict) -> Config:
    return parse_config(erp_config(**sections))


# --- filter: ERPLAB semantics ---------------------------------------------------------------


def _impulse_gain(pre: PreprocSection, freqs: list[float]) -> list[float]:
    n, sfreq = 2**17, 250.0
    x = np.zeros((1, n))
    x[0, n // 2] = 1.0
    raw = mne.io.RawArray(x, mne.create_info(["Cz"], sfreq, "eeg"), verbose=False)
    apply_filter(raw, pre)
    gain = np.abs(np.fft.rfft(raw.get_data()[0]))
    f = np.fft.rfftfreq(n, 1 / sfreq)
    return [float(gain[np.argmin(np.abs(f - q))]) for q in freqs]


def test_iir_matches_erplab_half_amplitude_and_slope() -> None:
    """ERPLAB order 2 = forward-backward first-order Butterworth: |H| = f^2 / (f^2 + fc^2),
    i.e. half amplitude at the cutoff and 12 dB/oct far below it."""
    freqs = [0.025, 0.05, 0.1, 0.2, 1.0]
    measured = _impulse_gain(PreprocSection(), freqs)
    expected = [f**2 / (f**2 + 0.1**2) for f in freqs]
    np.testing.assert_allclose(measured, expected, atol=0.02)


def test_lowpass_and_order_validation() -> None:
    pre = PreprocSection(highpass_hz=None, lowpass_hz=30.0)
    [at_cut] = _impulse_gain(pre, [30.0])
    assert at_cut == pytest.approx(0.5, abs=0.02)
    with pytest.raises(ValueError, match="even"):
        parse_config(erp_config(preproc={"filter": {"iir_order": 3}}))
    assert apply_filter(
        mne.io.RawArray(np.zeros((1, 100)), mne.create_info(1, 100.0, "eeg"), verbose=False),
        PreprocSection(highpass_hz=None),
    ) == {"applied": False}


def test_fir_and_causal_filters_run() -> None:
    for spec in ({"method": "fir"}, {"method": "iir", "phase": "minimum", "iir_order": 3}):
        pre = PreprocSection.model_validate({"filter": spec, "highpass_hz": 1.0})
        [dc] = _impulse_gain(pre, [0.0])
        assert dc < 1e-3


# --- line noise ------------------------------------------------------------------------------


def test_notch_reduces_line_peak_by_20_db(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth)
    before = line_peak_db(raw, 50.0)
    out = remove_line_noise(raw, LineNoiseSpec(method="notch"), 50.0)
    assert out["freqs"] == [50.0, 100.0, 150.0, 200.0]
    assert before - line_peak_db(raw, 50.0) >= 20


def test_line_noise_options() -> None:
    assert harmonics(60.0, 250.0) == [60.0, 120.0]
    raw = mne.io.RawArray(np.zeros((1, 1000)), mne.create_info(1, 250.0, "eeg"), verbose=False)
    with pytest.raises(PreprocError, match="no line frequency"):
        remove_line_noise(raw, LineNoiseSpec(method="notch"), None)
    assert remove_line_noise(raw, LineNoiseSpec(method="notch", freqs=[50, 200]), None)[
        "freqs"
    ] == [50]


def test_zapline_without_meegkit_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real = builtins.__import__

    def fake(name: str, *a: object, **k: object) -> object:
        if name.startswith("meegkit"):
            raise ImportError(name)
        return real(name, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake)
    raw = mne.io.RawArray(np.zeros((1, 1000)), mne.create_info(1, 250.0, "eeg"), verbose=False)
    with pytest.raises(PreprocError, match="pip install meegkit"):
        remove_line_noise(raw, LineNoiseSpec(method="zapline"), 50.0)


# --- bad channels and interpolation -----------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("method", ["pyprep_full", "pyprep_ransac"])
def test_pyprep_finds_dead_and_noisy_channel(erp_truth: ErpTruth, method: str) -> None:
    raw = _raw(erp_truth)
    remove_line_noise(raw, LineNoiseSpec(method="notch"), 50.0)
    bads = detect_bads(raw, BadChannelsSpec(method=method))  # type: ignore[arg-type]
    if method == "pyprep_full":
        assert set(bads) == {erp_truth.dead, erp_truth.noisy}
    else:  # RANSAC alone: the noisy channel is predicted badly from its neighbours
        assert erp_truth.noisy in bads
    assert all(r.startswith("pyprep") for r in bads.values())


def test_detect_bads_keeps_rawdata_bads_and_needs_positions(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth, montage=False)
    raw.info["bads"] = ["O1"]
    assert detect_bads(raw, BadChannelsSpec()) == {"O1": "rawdata channels.tsv"}
    with pytest.raises(PreprocError, match="electrode positions"):
        detect_bads(raw, BadChannelsSpec(method="pyprep_ransac"))
    with pytest.raises(PreprocError, match="electrode positions"):
        interpolate(raw, ["O1"], enabled=True)
    assert interpolate(raw, ["O1"], enabled=False) == []
    assert raw.info["bads"] == ["O1"]


def test_lof_without_sklearn_refuses(monkeypatch: pytest.MonkeyPatch, erp_truth: ErpTruth) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(PreprocError, match="scikit-learn"):
        detect_bads(_raw(erp_truth), BadChannelsSpec(method="lof"))


def test_interpolation_restores_dead_channel(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth)
    assert interpolate(raw, [erp_truth.dead], enabled=True) == [erp_truth.dead]
    assert raw.info["bads"] == []
    sd = raw.get_data(picks=[erp_truth.dead]).std() * 1e6
    assert sd > 1.0  # was 0.01 µV


# --- re-reference ----------------------------------------------------------------------------


def test_average_reference_sums_to_zero_and_spares_eog(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth)
    eog_before = raw.get_data(picks="eog")
    out = rereference(raw, ReferenceSection(), reference_free=True)
    assert out["scheme"] == "average"
    assert np.abs(raw.get_data(picks="eeg").sum(axis=0)).max() < 1e-12
    np.testing.assert_array_equal(raw.get_data(picks="eog"), eog_before)


def test_channel_reference_with_misc_mastoids(erp_truth: ErpTruth) -> None:
    """BioSemi EXG mastoids are typed `misc` by Phase 1; they can still be the reference."""
    raw = _raw(erp_truth)
    raw.set_channel_types({"HEOG": "misc"})
    raw.rename_channels({"HEOG": "EXG1"})
    expect = raw.get_data(picks=["Cz"])[0] - raw.get_data(picks=["EXG1"])[0]
    rereference(raw, ReferenceSection(scheme="channels", channels=["EXG1"]), reference_free=True)
    np.testing.assert_allclose(raw.get_data(picks=["Cz"])[0], expect, atol=1e-15)
    with pytest.raises(PreprocError, match="not in the data"):
        rereference(raw, ReferenceSection(scheme="channels", channels=["M1"]), reference_free=False)
    raw.info["bads"] = ["P9"] if "P9" in raw.ch_names else ["Pz"]
    with pytest.raises(PreprocError, match="are bad"):
        rereference(
            raw,
            ReferenceSection(scheme="channels", channels=raw.info["bads"]),
            reference_free=False,
        )


def test_no_reference_refused_only_when_reference_free(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth)
    assert rereference(raw, ReferenceSection(scheme="none"), reference_free=False) == {
        "scheme": "none"
    }
    with pytest.raises(PreprocError, match="reference-free"):
        rereference(raw, ReferenceSection(scheme="none"), reference_free=True)
    assert is_reference_free({"EEGReference": "CMS/DRL (reference-free)"})
    assert not is_reference_free({"EEGReference": "Cz"})


@pytest.mark.slow
def test_rest_reference_runs(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth).crop(0, 5)
    raw.info["bads"] = []
    raw.pick(["eeg"], exclude=[erp_truth.dead])
    assert (
        rereference(raw, ReferenceSection(scheme="rest"), reference_free=True)["scheme"] == "rest"
    )


# --- the whole preproc helper and its QA ------------------------------------------------------


def _ids(checks: list) -> dict[str, str]:
    return {c.id: c.level for c in checks}


def test_preprocess_qa_ids(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth)
    raw.info["bads"] = [erp_truth.dead, "PO8"]
    res = preprocess(
        raw,
        _cfg(preproc={"line_noise": {"method": "notch"}}),
        reference_free=False,
        roi_channels={"n170": ["PO8"]},
    )
    assert _ids(res.qa) == {
        "PREPROC_LINE_NOISE_RESIDUAL": "pass",
        "PREPROC_BADS_FRACTION": "pass",
        "PREPROC_ROI_INTERPOLATED": "warn",
        "REREF_APPLIED": "pass",
    }
    assert res.interpolated == [erp_truth.dead, "PO8"]
    assert not res.failed


def test_rereference_required_on_biosemi_style_data(erp_truth: ErpTruth) -> None:
    res = preprocess(_raw(erp_truth), _cfg(reference={"scheme": "none"}), reference_free=True)
    assert _ids(res.qa)["REREF_REQUIRED"] == "fail"
    assert res.failed and "reference" not in res.steps


def test_too_many_bads_fails(erp_truth: ErpTruth) -> None:
    raw = _raw(erp_truth)
    raw.info["bads"] = list(erp_truth.names[:9])
    res = preprocess(raw, _cfg(), reference_free=False)
    assert _ids(res.qa)["PREPROC_BADS_FRACTION"] == "fail" and res.failed
    assert res.interpolated == []


def test_line_residual_warns_without_cleaning(erp_truth: ErpTruth) -> None:
    res = preprocess(_raw(erp_truth), _cfg(), reference_free=False)
    assert _ids(res.qa)["PREPROC_LINE_NOISE_RESIDUAL"] == "warn"


# --- the stage, end to end on converted rawdata ------------------------------------------------


def test_stage_writes_outputs_and_caches(erp_project: Make) -> None:
    root, cfg = erp_project(preproc={"line_noise": {"method": "notch"}, "lowpass_hz": 40})
    [out] = Runner(cfg, root).run()
    assert out.status == "ok", out.message
    fif = paths.stage_path(
        root, "preproc", cfg, "01", description="preproc", suffix="eeg", extension=".fif"
    )
    raw = mne.io.read_raw_fif(fif, verbose=False)
    assert raw.info["highpass"] == pytest.approx(0.1) and raw.info["lowpass"] == pytest.approx(40)
    assert raw.info["custom_ref_applied"]
    tsv = pd.read_csv(fif.with_name(fif.name.replace("_eeg.fif", "_channels.tsv")), sep="\t")
    assert {"name", "type", "status", "reason", "interpolated"} <= set(tsv.columns)
    qa_json = json.loads(
        fif.with_name(fif.name.replace("_eeg.fif", "_qa.json")).read_text(encoding="utf-8")
    )
    assert qa_json["stage"] == "preproc" and qa_json["steps"]["line_noise"]["method"] == "notch"
    [again] = Runner(cfg, root).run()
    assert again.status == "skipped"
    cfg2 = parse_config(erp_config(preproc={"line_noise": {"method": "notch"}, "lowpass_hz": 30}))
    assert Runner(cfg2, root).status()[0].status == "stale"


def test_stage_failure_writes_qa_but_no_eeg(erp_project: Make) -> None:
    root, cfg = erp_project(reference={"scheme": "none"})
    sidecar = next((root / "rawdata").rglob("*_eeg.json"))
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["EEGReference"] = "CMS/DRL (reference-free)"
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    [out] = Runner(cfg, root).run()
    assert out.status == "failed" and "reference-free" in out.message
    folder = paths.subject_dir(root, "preproc", cfg, "01")
    names = sorted(p.name.split("desc-preproc_")[1] for p in folder.iterdir())
    assert names == ["channels.tsv", "provenance.json", "qa.json"]
    assert Runner(cfg, root).status()[0].status == "failed"


def test_stage_hash_includes_used_component_rois(erp_project: Make) -> None:
    root, cfg = erp_project()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        Runner(cfg, root).run()
    cfg2 = parse_config(erp_config(components={"use": ["n170"]}))
    assert Runner(cfg2, root).status()[0].status == "stale"
