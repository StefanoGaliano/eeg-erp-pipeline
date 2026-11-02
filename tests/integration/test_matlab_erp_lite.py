"""Phase M2: ERP-lite N170 parity, Python vs MATLAB end to end (BLUEPRINT_PHASES section 11).

ERP CORE N170 sub-015 with the ``erp_lite`` recipe (the dataset's recipe without ICA,
resampling and interpolation). Python: ``eegunify convert`` + the ERP stages. MATLAB, in one
session and from the same config dump: ``eegunify.convert`` (M1) and ``eegunify.erp_lite``
(bipolar EOG, ERPLAB ``pop_basicfilter``, re-reference, ERPLAB epochs/rejection/averages/
``pop_geterpvalues``). Tolerances (section 11):

- filtered, re-referenced continuous data: |diff| < 0.1 µV (good EEG and EOG channels; see EDGE_S);
- rejected epochs: identical sets; trials per condition identical;
- condition and contrast ERPs: RMS diff < 0.05 µV per good EEG and EOG channel;
- N170 mean amplitude (110-150 ms, PO8) per wave: |diff| < 0.05 µV.

Needs MATLAB with EEGLAB, ERPLAB and FieldTrip (``EEGUNIFY_MATLAB_SETUP``); skipped
otherwise, as in CI. EOG channels are compared too: this test found that the Python preproc
did not filter them (fixed 2026-11-02; docs/validation_erp.md). Run with ``-s`` to print the
numbers recorded in docs/validation_erp.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
import pytest
import scipy.io

from eegunify.config.dump import dump_config
from eegunify.config.loader import load_config
from eegunify.datasets import fetch_erpcore
from eegunify.erp import paths
from eegunify.erp.recipes import apply_recipe
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
CONFIG = PROJECT / "code" / "configs" / "erpcore_n170.yaml"
SUBJECT = "015"
CONTINUOUS_TOL_UV = 0.1
ERP_RMS_TOL_UV = 0.05
MEAN_TOL_UV = 0.05
#: Seconds left out at each end of the continuous comparison: zero-phase IIR filtering pads
#: the edges differently in MNE (scipy sosfiltfilt) and ERPLAB (MATLAB filtfilt); with a
#: 0.1 Hz high-pass the start-up transients differ for a few seconds. No event is that close
#: to an edge, so epochs and ERPs are compared in full.
EDGE_S = 10.0


def _matlab() -> str | None:
    return os.environ.get("EEGUNIFY_MATLAB") or shutil.which("matlab")


@pytest.fixture(scope="module")
def m2(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if _matlab() is None:
        pytest.skip("MATLAB not available")
    if "EEGUNIFY_MATLAB_SETUP" not in os.environ:
        pytest.skip("EEGUNIFY_MATLAB_SETUP (toolbox setup script) not set")
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", [SUBJECT])
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erp_lite")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    cfg = apply_recipe(load_config(CONFIG), "erp_lite")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(cfg, subjects=[SUBJECT], project_root=root, qa=False)
        assert res.error is None, res.error
        outs = {o.item.stage: o for o in Runner(cfg, root).run(subjects=[SUBJECT])}
    assert outs["erp"].status == "ok", outs["erp"].message

    dump = dump_config(cfg, subject=SUBJECT, resolved=True, project_root=root)
    dump_path = root / "config_dump.json"
    dump_path.write_text(json.dumps(dump), encoding="utf-8")
    mat_bids, mat_erp = root / "matlab_bids", root / "matlab_erp"
    setup = os.environ["EEGUNIFY_MATLAB_SETUP"]
    script = (
        f"addpath('{PROJECT / 'matlab'}'); eegunify.setup('{setup}'); "
        f"v = eegunify.convert('{dump_path}', 'root', '{root}', 'bids_root', '{mat_bids}'); "
        f"eegunify.erp_lite(v, '{dump_path}', '{mat_erp}');"
    )
    proc = subprocess.run(
        [_matlab(), "-batch", script],  # type: ignore[list-item]
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
        stdin=subprocess.DEVNULL,  # MATLAB can block on an inherited stdin
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]

    def stage(name: str, desc: str, suffix: str, ext: str) -> Path:
        return paths.stage_path(
            root, name, cfg, SUBJECT, description=desc, suffix=suffix, extension=ext
        )

    return {
        "cfg": cfg,
        "hash": dump["config_hash"],
        "py_preproc": stage("preproc", "preproc", "eeg", ".fif"),
        "py_log": stage("epochs", "stim", "log", ".tsv"),
        "py_ave": stage("erp", "stim", "ave", ".fif"),
        "py_measures": stage("erp", "stim", "measures", ".tsv"),
        "mat_preproc": mat_erp / "preproc.set",
        "mat_erp": mat_erp / "erp.mat",
        "mat": json.loads((mat_erp / "erp_lite.json").read_text(encoding="utf-8")),
    }


def test_continuous_after_filter_and_reference(m2: dict[str, Any]) -> None:
    py = mne.io.read_raw_fif(m2["py_preproc"], preload=True, verbose="error")
    mat = mne.io.read_raw_eeglab(m2["mat_preproc"], preload=True, verbose="error")
    good = [py.ch_names[i] for i in mne.pick_types(py.info, eeg=True, eog=True, exclude="bads")]
    assert set(good) <= set(mat.ch_names)
    edge = int(EDGE_S * py.info["sfreq"])
    a = py.get_data(picks=good)[:, edge:-edge] * 1e6
    b = mat.get_data(picks=good)[:, edge:-edge] * 1e6
    diff = float(np.abs(a - b).max())
    print(f"\nM2 continuous max |diff| {diff:.4g} µV ({len(good)} channels)")
    assert diff < CONTINUOUS_TOL_UV


def _py_log(m2: dict[str, Any]) -> pd.DataFrame:
    log = pd.read_csv(m2["py_log"], sep="\t")
    waves = m2["mat"]["waves"][:2]
    conds = m2["cfg"].conditions
    keep = log["trial_type"].isin([t for w in waves for t in conds[w]])
    return log[keep].reset_index(drop=True)


def test_rejected_epochs_identical(m2: dict[str, Any]) -> None:
    log = _py_log(m2)
    sfreq = m2["cfg"].preproc.resample_hz or 1024.0
    # NO_RESPONSE events are in no ERPLAB bin, so ERPLAB never epochs or flags them.
    rejected = log[~log["kept"] & (log["reason"] != "NO_RESPONSE")]
    ours = sorted(np.round(rejected["onset_s"] * sfreq).astype(int).tolist())
    theirs = sorted(np.atleast_1d(m2["mat"]["rejected_samples"]).astype(int).tolist())
    print(f"\nM2 rejected: {len(ours)} of {len(log)} face/car epochs")
    assert theirs == ours


def test_trials_per_condition(m2: dict[str, Any]) -> None:
    log = _py_log(m2)
    kept = log[log["kept"]]
    conds = m2["cfg"].conditions
    ours = [int(kept["trial_type"].isin(conds[w]).sum()) for w in m2["mat"]["waves"][:2]]
    assert list(np.atleast_1d(m2["mat"]["trials_per_bin"])[:2]) == ours


def test_erp_waveforms(m2: dict[str, Any]) -> None:
    mat = scipy.io.loadmat(m2["mat_erp"], squeeze_me=True)
    labels = [str(x) for x in np.atleast_1d(mat["erp_labels"])]
    times = np.asarray(mat["times_ms"], dtype=float)
    evokeds = {e.comment: e for e in mne.read_evokeds(m2["py_ave"], verbose="error")}
    worst = 0.0
    for b, wave in enumerate(m2["mat"]["waves"]):
        ev = evokeds[wave]
        eeg = [ev.ch_names[i] for i in mne.pick_types(ev.info, eeg=True, eog=True, exclude="bads")]
        good = [c for c in eeg if c in labels]
        py_t = ev.times * 1000
        common = np.intersect1d(np.round(py_t, 6), np.round(times, 6))
        assert common.size > 0.9 * py_t.size
        pi = np.searchsorted(np.round(py_t, 6), common)
        mi = np.searchsorted(np.round(times, 6), common)
        a = ev.copy().pick(good).data[:, pi] * 1e6
        b_ = mat["bindata"][[labels.index(c) for c in good]][:, mi, b]
        rms = np.sqrt(np.mean((a - b_) ** 2, axis=1))
        worst = max(worst, float(rms.max()))
    print(f"\nM2 ERP RMS diff, worst channel and wave: {worst:.4g} µV")
    assert worst < ERP_RMS_TOL_UV


def test_mean_amplitude(m2: dict[str, Any]) -> None:
    m = pd.read_csv(m2["py_measures"], sep="\t")
    comp = m2["mat"]["component"]
    prim = m.query(
        "roi == 'primary' and measure == 'mean_amplitude' and component == @comp"
    ).set_index("wave")["value"]
    ours = [float(prim[w]) for w in m2["mat"]["waves"]]
    theirs = [float(x) for x in np.atleast_1d(m2["mat"]["mean_uv"])]
    print(f"\nM2 {comp} mean amplitude µV: python {ours} matlab {theirs}")
    np.testing.assert_allclose(theirs, ours, atol=MEAN_TOL_UV)


def test_config_hash_round_trip(m2: dict[str, Any]) -> None:
    assert m2["mat"]["config_hash"] == m2["hash"]
