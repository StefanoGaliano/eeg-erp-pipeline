"""M-E5: eegunify vs ERPLAB on the same preprocessed data (docs/BLUEPRINT_PHASES.md 8.4).

ERP CORE N170 sub-015 goes through eegunify with the ``erplab`` recipe (the ERP CORE config
without ICA). The preprocessed data (``desc-preproc``) are exported to
EEGLAB and run through ERPLAB by ``matlab/erplab_crosscheck.m``: binlister, ``pop_epochbin``,
``pop_artmwppth``, ``pop_artstep`` (HEOG), ``pop_averager``, ``pop_binoperator``,
``pop_geterpvalues``. Expected:
identical rejected epochs and trial counts, and mean amplitudes within 0.05 µV.

Needs MATLAB with EEGLAB and ERPLAB, set up by ``EEGUNIFY_MATLAB_SETUP`` (e.g.
Practice/setup_eeg_toolboxes.m); skipped otherwise, as in CI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import warnings
from pathlib import Path

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.config.loader import load_config
from eegunify.datasets import fetch_erpcore
from eegunify.erp import paths
from eegunify.erp.recipes import apply_recipe
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
CONFIG = PROJECT / "code" / "configs" / "erpcore_n170.yaml"
BINS = [("face", "1-40"), ("car", "41-80")]
TOL_UV = 0.05


def _matlab() -> str | None:
    return os.environ.get("EEGUNIFY_MATLAB") or shutil.which("matlab")


@pytest.fixture(scope="module")
def project(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", ["015"])
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erplab_crosscheck")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(CONFIG, subjects=["015"], project_root=root)
    assert res.error is None, res.error
    return root


def _m_struct(p: dict[str, object]) -> str:
    def val(v: object) -> str:
        if isinstance(v, str):
            return f"'{v}'"
        if isinstance(v, list) and v and isinstance(v[0], tuple):
            return "{" + ", ".join("{" + ", ".join(f"'{x}'" for x in t) + "}" for t in v) + "}"
        if isinstance(v, list) and v and isinstance(v[0], str):
            return "{" + ", ".join(f"'{x}'" for x in v) + "}"
        if isinstance(v, list):
            return "[" + " ".join(f"{x:g}" for x in v) + "]"
        return f"{v:g}"  # type: ignore[str-format]

    return "struct(" + ", ".join(f"'{k}', {{{val(v)}}}" for k, v in p.items()) + ")"


@pytest.mark.skipif(_matlab() is None, reason="MATLAB not available")
@pytest.mark.skipif(
    "EEGUNIFY_MATLAB_SETUP" not in os.environ,
    reason="EEGUNIFY_MATLAB_SETUP (toolbox setup script) not set",
)
def test_erplab_matches_sub015(project: Path, tmp_path: Path) -> None:
    cfg = apply_recipe(load_config(CONFIG), "erplab")
    outs = {o.item.stage: o for o in Runner(cfg, project).run(subjects=["015"])}
    assert outs["erp"].status == "ok", outs["erp"].message

    pre = paths.stage_path(
        project, "preproc", cfg, "015", description="preproc", suffix="eeg", extension=".fif"
    )
    raw = mne.io.read_raw_fif(pre, preload=True, verbose=False)
    setfile = tmp_path / "preproc.set"
    mne.export.export_raw(setfile, raw, fmt="eeglab", overwrite=True, verbose="error")
    log = pd.read_csv(
        paths.stage_path(
            project, "epochs", cfg, "015", description="stim", suffix="log", extension=".tsv"
        ),
        sep="\t",
    )
    log = log[log["trial_type"].isin(["stimulus/face", "stimulus/car"])].reset_index(drop=True)
    events = pd.DataFrame(
        {
            "sample": np.round(log["onset_s"] * raw.info["sfreq"]).astype(int) + 1,
            "code": log["code"],
        }
    )
    events_tsv = tmp_path / "events.tsv"
    events.to_csv(events_tsv, sep="\t", index=False, encoding="utf-8")

    eset, rej = cfg.epochs.sets["stim"], cfg.epochs.reject
    params = {
        "epoch_ms": [eset.tmin_ms, eset.tmax_ms],
        "baseline_ms": list(eset.baseline_ms or (eset.tmin_ms, 0.0)),
        "ptp_uv": rej.ptp_uv,
        "window_ms": rej.ptp_window_ms,
        "step_ms": rej.ptp_step_ms,
        "measure_ms": [110.0, 150.0],
        "channel": "PO8",
        "eeg_channels": [raw.ch_names[i] for i in mne.pick_types(raw.info, eeg=True)],
        "bins": BINS,
        "step_uv": rej.step_heog_uv or 0,
        "heog": "HEOG",
        "step_window_ms": rej.step_window_ms,
        "step_step_ms": rej.step_step_ms,
    }
    out_json = tmp_path / "erplab.json"
    setup = Path(os.environ["EEGUNIFY_MATLAB_SETUP"])
    script = (
        f"cd('{setup.parent}'); evalc('{setup.stem}'); addpath('{PROJECT / 'matlab'}'); "
        f"erplab_crosscheck('{setfile}', '{events_tsv}', '{out_json}', {_m_struct(params)});"
    )
    proc = subprocess.run(
        [_matlab(), "-batch", script],  # type: ignore[list-item]
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    theirs = json.loads(out_json.read_text(encoding="utf-8"))

    ours_rejected = sorted(int(i) + 1 for i in np.flatnonzero(~log["kept"].to_numpy()))
    assert sorted(np.atleast_1d(theirs["rejected_events"]).astype(int).tolist()) == ours_rejected
    kept = log[log["kept"]]
    assert list(np.atleast_1d(theirs["trials_per_bin"])[:2]) == [
        int((kept["trial_type"] == "stimulus/face").sum()),
        int((kept["trial_type"] == "stimulus/car").sum()),
    ]
    m = pd.read_csv(
        paths.stage_path(
            project, "erp", cfg, "015", description="stim", suffix="measures", extension=".tsv"
        ),
        sep="\t",
    )
    prim = m.query("roi == 'primary' and measure == 'mean_amplitude'").set_index("wave")["value"]
    ours = [prim["face"], prim["car"], prim["face_minus_car"]]
    np.testing.assert_allclose(theirs["mean_uv"], ours, atol=TOL_UV)
