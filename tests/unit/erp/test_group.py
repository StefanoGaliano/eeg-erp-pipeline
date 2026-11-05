"""group stage (M-E6a, BLUEPRINT 6.7 / 8.1): stats against scipy, cluster test, QA, stage."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from eegunify.channels.montage import _standard
from eegunify.config.loader import parse_config
from eegunify.config.schema import Config
from eegunify.erp import paths, qa
from eegunify.erp.group import (
    cluster_test,
    effect_size,
    grand_averages,
    min_permutation_p,
    stats_table,
)
from eegunify.erp.runner import Runner
from tests.synthetic.erp import erp_config

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


def _cfg(**group: Any) -> Config:
    return parse_config(erp_config(**SECTIONS, group=group))


def _measures(face: np.ndarray, car: np.ndarray) -> pd.DataFrame:
    rows = []
    for i, (f, c) in enumerate(zip(face, car, strict=True)):
        sub = f"{i + 1:02d}"
        for wave, v in (("face", f), ("car", c), ("face_minus_car", f - c)):
            for measure, unit, val in (
                ("mean_amplitude", "uV", v),
                ("peak_latency", "ms", 130.0 + i),
            ):
                rows.append(
                    {
                        "subject": sub,
                        "component": "n170",
                        "wave": wave,
                        "roi": "primary",
                        "measure": measure,
                        "unit": unit,
                        "value": val,
                    }
                )
                rows.append({**rows[-1], "roi": "secondary", "value": 99.0})
    return pd.DataFrame(rows)


def test_stats_match_scipy_paired_tests() -> None:
    rng = np.random.default_rng(3)
    face = rng.normal(3.0, 1.0, 10)
    car = face + rng.normal(1.5, 1.0, 10)  # car more positive: face - car negative
    t = stats_table(_measures(face, car), _cfg(), ["face_minus_car"])
    tested = t[(t["wave"] == "face_minus_car") & (t["measure"] == "mean_amplitude")]
    tt = tested.set_index("test")
    ref_t = stats.ttest_rel(face, car)
    assert tt.loc["paired_t", "statistic"] == pytest.approx(ref_t.statistic)
    assert tt.loc["paired_t", "p"] == pytest.approx(ref_t.pvalue)
    ref_w = stats.wilcoxon(face, car)
    assert tt.loc["wilcoxon", "p"] == pytest.approx(ref_w.pvalue)
    d = face - car
    assert tt.loc["paired_t", "d_z"] == pytest.approx(d.mean() / d.std(ddof=1))
    lo, hi = tt.loc["paired_t", ["d_z_ci_low", "d_z_ci_high"]]
    assert lo < tt.loc["paired_t", "d_z"] < hi < 0
    assert tt.loc["paired_t", "n"] == 10
    assert tt.loc["paired_t", "mean"] == pytest.approx(d.mean())
    # conditions and latencies are descriptive only; secondary ROI rows never used
    other = t[~((t["wave"] == "face_minus_car") & (t["measure"] == "mean_amplitude"))]
    assert set(other["test"]) == {"descriptive"}
    lat = other[(other["wave"] == "face") & (other["measure"] == "peak_latency")].iloc[0]
    assert lat["mean"] == pytest.approx(134.5)


def test_effect_size_is_seeded_and_handles_small_n() -> None:
    x = np.array([-1.0, -2.0, -0.5, -1.5, -2.5])
    assert effect_size(x, 1000, 1) == effect_size(x, 1000, 1)
    assert np.isnan(effect_size(np.array([1.0]), 1000, 1)[0])
    dz, lo, hi = effect_size(np.array([1.0, 2.0]), 1000, 1)
    assert np.isfinite(dz) and np.isnan(lo) and np.isnan(hi)
    # 5 subjects: d_z, but no CI (degenerate bootstrap); 8: a finite CI around d_z
    assert np.isnan(effect_size(x, 1000, 1)[1])
    y = np.array([-1.0, -2.0, -0.5, -1.5, -2.5, -1.2, -0.8, -1.9])
    dz, lo, hi = effect_size(y, 2000, 1)
    assert lo < dz < hi < 0


def test_min_permutation_p_and_underpowered_note() -> None:
    assert min_permutation_p(5, 0) == pytest.approx(2 / 32)
    assert min_permutation_p(5, -1) == pytest.approx(1 / 32)
    c = qa.underpowered_check(5, min_permutation_p(5, 0), 0.05, 0)
    assert c.level == "pass" and "no cluster can reach significance" in c.message
    c = qa.underpowered_check(10, min_permutation_p(10, 0), 0.05, 0)
    assert "<= alpha" in c.message


def test_group_qa_checks() -> None:
    assert qa.subjects_excluded_check({}).level == "pass"
    c = qa.subjects_excluded_check({"016": "epochs failed: fewer than 30"})
    assert c.level == "warn" and "sub-016" in c.message
    assert qa.reference_values_check(0, 4, []).level == "pass"
    assert qa.reference_values_check(1, 4, ["017 face_minus_car"]).level == "warn"


def _subject_evokeds(n: int, effect_uv: float, seed: int) -> list[mne.Evoked]:
    """``n`` contrast waves on 16 channels: effect over PO8/P8/PO4/O2 at 100-160 ms."""
    names = ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4", "O1", "O2"]
    names += ["P7", "P8", "PO7", "PO8", "PO3", "PO4"]
    info = mne.create_info(names, 250.0, "eeg")
    rng = np.random.default_rng(seed)
    times = np.arange(-50, 150) / 250.0
    out = []
    for _ in range(n):
        data = rng.normal(0, 0.5, (len(names), len(times)))
        mask = (times >= 0.1) & (times <= 0.16)
        for ch in ("PO8", "P8", "PO4", "O2"):
            data[names.index(ch), mask] += effect_uv
        ev = mne.EvokedArray(data * 1e-6, info, tmin=times[0], nave=40, comment="face_minus_car")
        ev.set_montage(_standard("standard_1020"))
        out.append(ev)
    return out


def test_cluster_found_in_injected_window() -> None:
    cfg = _cfg(
        cluster={
            "enabled": True,
            "contrast": "face_minus_car",
            "tmin_ms": 0,
            "tmax_ms": 400,
            "n_permutations": 1024,
            "tail": -1,
        }
    )
    table, info = cluster_test(_subject_evokeds(10, -3.0, 0), cfg)
    best = table.iloc[0]
    assert best["p"] < 0.05
    assert best["tmin_ms"] >= 90 and best["tmax_ms"] <= 170
    assert "PO8" in best["channels"].split("+")
    assert info["n_permutations"] == 1024 and info["min_possible_p"] == pytest.approx(1 / 1024)


def test_cluster_with_five_subjects_cannot_reach_alpha() -> None:
    cfg = _cfg(cluster={"enabled": True, "contrast": "face_minus_car", "tail": 0})
    table, info = cluster_test(_subject_evokeds(5, -3.0, 1), cfg)
    assert info["n_permutations"] == 32  # all sign flips
    assert (table["p"] >= 2 / 32 - 1e-12).all()


def test_grand_average_per_wave() -> None:
    evs = _subject_evokeds(3, -3.0, 2)
    ga = grand_averages({f"0{i}": {"face_minus_car": ev} for i, ev in enumerate(evs)})
    expected = np.mean([ev.data for ev in evs], axis=0)
    np.testing.assert_allclose(ga["face_minus_car"].data, expected)
    assert ga["face_minus_car"].comment == "face_minus_car"


def test_group_stage_end_to_end(blink_project: Make) -> None:
    root, cfg = blink_project(**SECTIONS, group={"cluster": {"enabled": False}, "n_bootstrap": 200})
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["group"].status == "ok", outs["group"].message
    out = paths.group_dir(root, cfg)
    task = cfg.dataset.task
    for name in ("measures.tsv", "stats.tsv", "desc-stim_ave.fif", "qa.json"):
        assert (out / f"task-{task}_{name}").exists(), name
    q = json.loads((out / f"task-{task}_qa.json").read_text(encoding="utf-8"))
    ids = {c["id"]: c for c in q["checks"]}
    assert ids["GROUP_SUBJECTS_EXCLUDED"]["level"] == "pass"
    assert "GROUP_UNDERPOWERED" in ids and "REFERENCE_VALUES_MISMATCH" not in ids
    # changing only group settings reruns only the group stage
    cfg2 = parse_config({**cfg.model_dump(mode="json"), "group": {"n_bootstrap": 300}})
    st = {it.stage: it.status for it in Runner(cfg2, root).status()}
    assert st["erp"] == "ok" and st["group"] == "stale"


def test_group_stage_refuses_unknown_erpcore_task(blink_project: Make) -> None:
    root, cfg = blink_project(**SECTIONS, group={"reference_values": "erpcore"})
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["group"].status == "failed"
    assert "no comparison defined for task" in outs["group"].message


def test_ds006171_cluster_one_wave_per_subject(blink_project: Make) -> None:
    # Start_Vision (ds006171) has two epoch sets with the same contrast (stim, stimraw): the
    # cluster test took the contrast from every set, so each subject counted twice (0.9.2).
    two_sets = {
        **SECTIONS,
        "epochs": {
            "sets": {
                "stim": {"lock": "stimulus/*"},
                "stimraw": {"lock": "stimulus/*", "baseline_ms": None},
            },
            "reject": {"min_trials_per_condition": 10},
        },
    }
    root, cfg = blink_project(
        **two_sets,
        group={"cluster": {"enabled": True, "contrast": "face_minus_car"}, "n_bootstrap": 200},
    )
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["group"].status == "ok", outs["group"].message
    out = paths.group_dir(root, cfg)
    # one subject = one wave: too few for a cluster test, so no table is written
    assert not (out / f"task-{cfg.dataset.task}_desc-face_minus_car_clusters.tsv").exists()
