"""ica stage (M-E2): synthetic blink removal (BLUEPRINT_PHASES 8.1), the exclusion rule, outputs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import mne
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from eegunify.channels.montage import _standard
from eegunify.config.erp_sections import (
    ICLABEL_CLASSES,
    ArtifactsSection,
    IcaFitExcludeSpec,
    IcaSpec,
)
from eegunify.config.loader import parse_config
from eegunify.config.schema import Config, ReferenceSection
from eegunify.erp import paths, qa
from eegunify.erp.ica import (
    IcaError,
    add_bipolar_eog,
    apply_ica,
    break_spans,
    decide,
    fit_copy,
    ptp_spans,
    run_ica,
)
from eegunify.erp.preprocess import PreprocError
from eegunify.erp.runner import Runner
from tests.synthetic.erp import ErpTruth, erp_config

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")
Make = Callable[..., tuple[Path, Config]]
AVG = ReferenceSection(scheme="average")


def _raw(truth: ErpTruth, data_uV: np.ndarray | None = None) -> mne.io.RawArray:
    data = truth.data_uV if data_uV is None else data_uV
    raw = mne.io.RawArray(
        data * 1e-6, mne.create_info(truth.names, truth.sfreq, truth.types), verbose=False
    )
    raw.set_montage(_standard("standard_1020"), verbose=False)
    return raw


@pytest.fixture(scope="module")
def cleaned(blink_truth: ErpTruth) -> tuple[mne.io.BaseRaw, object]:
    raw = _raw(blink_truth)
    raw.set_eeg_reference("average", projection=False, verbose=False)
    return raw, run_ica(raw, ArtifactsSection(), AVG)


# --- synthetic ground truth ------------------------------------------------------------------


def _part(truth: ErpTruth, part: np.ndarray, res: object) -> tuple[np.ndarray, np.ndarray]:
    """``part`` (one additive component of the data) before and after the same cleaning.
    ICA cleaning and re-referencing are linear, so this isolates what happened to it."""
    raw = _raw(truth, part)
    raw.set_eeg_reference("average", projection=False, verbose=False)
    before = raw.get_data(picks="eeg")
    apply_ica(raw, res.ica, res.decision.excluded, AVG)  # type: ignore[attr-defined]
    return before, raw.get_data(picks="eeg")


def test_blink_component_found_and_removed(blink_truth: ErpTruth, cleaned: tuple) -> None:
    _, res = cleaned
    table = res.decision.table
    assert len(res.decision.excluded) >= 1
    assert (table.loc[table["excluded"], "iclabel"] == "eye blink").all()
    assert blink_truth.blink_uV is not None
    before, after = _part(blink_truth, blink_truth.blink_uV, res)
    assert (after**2).sum() / (before**2).sum() < 0.10  # >= 90 % of blink variance removed


def test_erp_at_po8_kept(blink_truth: ErpTruth, cleaned: tuple) -> None:
    _, res = cleaned
    erp = np.zeros_like(blink_truth.data_uV)
    wave_t = np.arange(int(0.4 * blink_truth.sfreq))
    from tests.synthetic.erp import AMPLITUDE_UV, N170_MS, N170_SIGMA_MS

    wave = np.exp(-(((wave_t / blink_truth.sfreq * 1000 - N170_MS) / N170_SIGMA_MS) ** 2) / 2)
    for s, c in zip(blink_truth.samples, blink_truth.conditions, strict=True):
        erp[:, s : s + wave.size] += AMPLITUDE_UV[c] * np.outer(blink_truth.topography, wave)
    before, after = _part(blink_truth, erp, res)
    po8 = blink_truth.index("PO8")
    assert np.abs(after[po8] - before[po8]).max() * 1e6 < 0.2


def test_cleaned_data_qa(cleaned: tuple) -> None:
    raw, res = cleaned
    checks = {c.id: c for c in qa.ica_checks(raw, res.decision)}
    assert checks["ICA_EXCLUDED_COUNT"].level == "pass"
    assert checks["ICA_BLINK_RESIDUAL"].level == "pass"
    assert checks["ICA_BRAIN_REMOVED"].level == "pass"
    assert res.steps["fit"]["reference"] == "average" and res.steps["fit"]["lowpass_hz"] == 100


# --- helpers ---------------------------------------------------------------------------------


def test_fit_copy_matches_iclabel_conditions(blink_truth: ErpTruth) -> None:
    raw = _raw(blink_truth)
    copy = fit_copy(raw, IcaSpec())
    assert copy.info["highpass"] == pytest.approx(1.0) and copy.info["lowpass"] == 100
    eeg = copy.get_data(picks="eeg")
    assert np.abs(eeg.mean(axis=0)).max() < 1e-12  # average reference
    assert raw.info["highpass"] == 0  # the original is untouched


def _probs(top: list[str], p: float = 0.9) -> np.ndarray:
    probs = np.full((len(top), len(ICLABEL_CLASSES)), (1 - p) / (len(ICLABEL_CLASSES) - 1))
    for i, cls in enumerate(top):
        probs[i, ICLABEL_CLASSES.index(cls)] = p
    return probs


def test_decide_rule() -> None:
    probs = _probs(["eye blink", "brain", "muscle artifact", "other", "brain"])
    probs[3] = _probs(["other"], 0.5)[0]
    z = np.array([[0.1, 4.0, 0.2, -3.5, 0.0]])
    spec = IcaSpec(exclude=[4], include=[0])
    d = decide(probs, ["VEOG"], z, spec)
    rows = d.table.set_index("idx")
    # 0: blink by ICLabel but manually included; 1: EOG vote ignored for brain;
    # 2: muscle not in exclude_classes; 3: EOG vote counts for non-brain; 4: manual.
    assert d.excluded == [3, 4]
    assert rows.loc[0, "reason"].startswith("manual include")
    assert rows.loc[3, "reason"] == "eog VEOG z=-3.5"
    assert rows.loc[1, "reason"] == "kept"
    assert {"p_eye_blink", "p_brain", "eog_z_VEOG", "iclabel_prob"} <= set(d.table.columns)


def test_decide_index_out_of_range() -> None:
    with pytest.raises(IcaError, match="only 2 components"):
        decide(_probs(["brain", "brain"]), [], np.empty((0, 2)), IcaSpec(exclude=[5]))


def test_unknown_iclabel_class_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown ICLabel classes"):
        IcaSpec.model_validate({"iclabel": {"exclude_classes": ["blink"]}})


def test_reference_none_refused(blink_truth: ErpTruth) -> None:
    with pytest.raises(IcaError, match=r"reference\.scheme"):
        run_ica(_raw(blink_truth), ArtifactsSection(), ReferenceSection(scheme="none"))


def test_channels_reference_restored(cleaned: tuple, blink_truth: ErpTruth) -> None:
    _, res = cleaned
    raw = _raw(blink_truth)
    ref = ReferenceSection(scheme="channels", channels=["O1", "O2"])
    apply_ica(raw, res.ica, res.decision.excluded, ref)
    o = raw.get_data(picks=["O1", "O2"])
    assert np.abs(o.sum(axis=0)).max() < 1e-12


def test_bipolar_eog(blink_truth: ErpTruth) -> None:
    raw = _raw(blink_truth)
    assert add_bipolar_eog(raw, {"VEOGb": ("Fp1", "VEOG")}) == ["VEOGb"]
    assert raw.get_channel_types(picks=["VEOGb"]) == ["eog"]
    np.testing.assert_allclose(
        raw.get_data(picks="VEOGb")[0],
        raw.get_data(picks="Fp1")[0] - raw.get_data(picks="VEOG")[0],
    )
    with pytest.raises(PreprocError, match="not in the data"):
        add_bipolar_eog(raw, {"X": ("Fp1", "nope")})


def test_asr_without_meegkit(blink_truth: ErpTruth, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "meegkit.asr", None)
    raw = _raw(blink_truth)
    raw.set_eeg_reference("average", projection=False, verbose=False)
    with pytest.raises(IcaError, match="meegkit"):
        run_ica(raw, ArtifactsSection.model_validate({"asr": {"cutoff": 20}}), AVG)


def test_excluded_count_and_brain_qa(cleaned: tuple) -> None:
    raw, res = cleaned
    table = res.decision.table.copy()
    table["excluded"] = False
    none = type(res.decision)(table, res.decision.eog_channels)
    [count, *_] = qa.ica_checks(raw, none)
    assert count.level == "warn" and "EOG present" in count.message
    brain = int(table.loc[table["iclabel"] == "brain", "idx"].iloc[0])
    table.loc[brain, "excluded"] = True
    brainy = type(res.decision)(table, res.decision.eog_channels)
    checks = {c.id: c for c in qa.ica_checks(raw, brainy)}
    assert checks["ICA_BRAIN_REMOVED"].level == "warn" and checks["ICA_BRAIN_REMOVED"].value == [
        brain
    ]


# --- stage -----------------------------------------------------------------------------------


def _out(root: Path, cfg: Config, desc: str, suffix: str, ext: str) -> Path:
    return paths.stage_path(root, "ica", cfg, "01", description=desc, suffix=suffix, extension=ext)


def test_stage_outputs_and_cache(blink_project: Make) -> None:
    root, cfg = blink_project()
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["ica"].status == "ok", outs["ica"].message
    tsv = pd.read_csv(_out(root, cfg, "ica", "components", ".tsv"), sep="\t")
    assert {"idx", "iclabel", "excluded", "reason", "eog_z_VEOG", "eog_z_HEOG"} <= set(tsv.columns)
    assert tsv["excluded"].sum() >= 1
    ica = mne.preprocessing.read_ica(_out(root, cfg, "ica", "ica", ".fif"), verbose=False)
    assert ica.exclude == list(tsv.loc[tsv["excluded"], "idx"])
    clean = mne.io.read_raw_fif(_out(root, cfg, "clean", "eeg", ".fif"), verbose=False)
    assert clean.info["custom_ref_applied"]
    data = json.loads(_out(root, cfg, "ica", "qa", ".json").read_text(encoding="utf-8"))
    assert data["stage"] == "ica" and data["steps"]["iclabel"]["reference"] == "average"
    assert {o.status for o in Runner(cfg, root).run()} == {"skipped"}
    # A manual choice only reruns ica, not preproc.
    cfg2 = parse_config(erp_config(artifacts={"ica": {"exclude": [1]}}))
    st = {it.stage: it.status for it in Runner(cfg2, root).status()}
    assert st == {"preproc": "ok", "ica": "stale"}


def test_stage_disabled_with_method_none(blink_project: Make) -> None:
    root, cfg = blink_project(artifacts={"method": "none"})
    assert [it.stage for it in Runner(cfg, root).status()] == ["preproc"]


def test_bipolar_eog_derived_in_preproc_without_ica(blink_project: Make) -> None:
    """The derivation happens in preproc, so HEOG exists even with ``artifacts.method: none``."""
    root, cfg = blink_project(artifacts={"method": "none", "eog_bipolar": {"HEOGb": ["F7", "F8"]}})
    [out] = Runner(cfg, root).run()
    assert out.status == "ok", out.message
    pre = paths.stage_path(
        root, "preproc", cfg, "01", description="preproc", suffix="eeg", extension=".fif"
    )
    raw = mne.io.read_raw_fif(pre, verbose=False)
    assert raw.get_channel_types(picks=["HEOGb"]) == ["eog"]


# --- data left out of the fit (ERP CORE Script #2) ---------------------------------------------


def _with_events(truth: ErpTruth, data_uV: np.ndarray | None = None) -> mne.io.RawArray:
    raw = _raw(truth, data_uV)
    on = truth.samples / truth.sfreq
    raw.set_annotations(mne.Annotations(on, 0.0, [f"stimulus/{c}" for c in truth.conditions]))
    return raw


def test_break_and_ptp_spans() -> None:
    sf = 100.0
    raw = mne.io.RawArray(np.zeros((2, 6000)), mne.create_info(2, sf, "eeg"), verbose=False)
    ev = [5.0, 6.0, 7.0, 20.0, 21.0, 50.0]  # gaps 7->20 and 21->50 are breaks
    raw.set_annotations(mne.Annotations([*ev, 30.0], 0.0, ["stimulus/a"] * 6 + ["response/x"]))
    spec = IcaFitExcludeSpec(ptp_uv=None)
    assert break_spans(raw, spec) == [(0.0, 3.0), (52.0, raw.times[-1]), (9.0, 18.0), (23.0, 48.0)]
    assert break_spans(raw, spec.model_copy(update={"break_selector": None})) == []
    raw._data[1, 1000:1010] = 1e-3  # 1000 µV for 0.1 s at 10 s
    spans = ptp_spans(raw, IcaFitExcludeSpec(break_selector=None))
    assert len(spans) == 1 and spans[0][0] <= 10.0 and spans[0][1] >= 10.1
    assert spans[0][1] - spans[0][0] <= 1.1  # window 0.5 s on each side at most
    with pytest.raises(IcaError, match="matches no event"):
        break_spans(raw, IcaFitExcludeSpec(break_selector="nothing"))


def test_erpcore_n170_sub035_burst_in_break_left_out_of_fit(blink_truth: ErpTruth) -> None:
    """sub-035: a ~35 mV whole-head burst in a break took 93 % of the PCA variance, ICA kept 2
    components and the blinks stayed. Here: the events of 20-40 s are removed (a break) and a
    30 mV burst with a broad random topography is put at 25-35 s."""
    t = blink_truth
    keep = (t.samples < 20 * t.sfreq) | (t.samples > 40 * t.sfreq)
    truth = ErpTruth(
        **{
            **t.__dict__,
            "samples": t.samples[keep],
            "conditions": [c for c, k in zip(t.conditions, keep, strict=True) if k],
        }
    )
    data = t.data_uV.copy()
    rng = np.random.default_rng(35)
    a, b = int(25 * t.sfreq), int(35 * t.sfreq)
    n_eeg = t.types.count("eeg")
    slow = np.cumsum(rng.standard_normal(b - a))
    slow -= slow.mean()
    data[:n_eeg, a:b] += 30000 * rng.standard_normal((n_eeg, 1)) * slow / np.abs(slow).max()
    spec = ArtifactsSection(ica=IcaSpec(fit_exclude=IcaFitExcludeSpec()))
    raw = _with_events(truth, data)
    raw.set_eeg_reference("average", projection=False, verbose=False)
    res = run_ica(raw, spec, AVG)
    fx = res.steps["fit_exclude"]
    assert fx["breaks_s"] > 10 and 0 < fx["excluded_fraction"] < 0.5
    assert qa.fit_excluded_check(fx).level == "pass"
    assert t.blink_uV is not None
    before, after = _part(t, t.blink_uV, res)
    assert (after**2).sum() / (before**2).sum() < 0.10
    raw = _with_events(truth, data)
    raw.set_eeg_reference("average", projection=False, verbose=False)
    with pytest.raises(RuntimeError, match="One PCA component"):  # without it: no usable fit
        run_ica(raw, ArtifactsSection(), AVG)
