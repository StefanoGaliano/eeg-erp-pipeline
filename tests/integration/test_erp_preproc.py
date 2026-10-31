"""M-E1 on real data: ERP CORE N170 sub-015 through the preproc stage.

Uses the ERP CORE recipe in ``code/configs/erpcore_n170.yaml`` (256 Hz, 0.1 Hz Butterworth
high-pass, F8 interpolated, average reference) and checks ``REREF_REQUIRED`` on the same
reference-free BioSemi recording with ``reference.scheme: none``.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.config.loader import load_config, parse_config
from eegunify.datasets import fetch_erpcore
from eegunify.erp import paths
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
CONFIG = Path(__file__).parents[2] / "code" / "configs" / "erpcore_n170.yaml"


@pytest.fixture(scope="module")
def project(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", ["015"])
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erpcore_preproc")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(CONFIG, subjects=["015"], project_root=root)
    assert res.error is None, res.error
    return root


def test_sub015_preproc_runs_with_erpcore_recipe(project: Path) -> None:
    cfg = load_config(CONFIG)
    [out] = Runner(cfg, project).run(subjects=["015"])
    assert out.status == "ok", out.message
    fif = paths.stage_path(
        project, "preproc", cfg, "015", description="preproc", suffix="eeg", extension=".fif"
    )
    raw = mne.io.read_raw_fif(fif, preload=True, verbose=False)
    assert raw.info["sfreq"] == 256
    assert raw.info["highpass"] == pytest.approx(0.1)
    assert raw.info["bads"] == []
    eeg = raw.get_data(picks="eeg")
    assert eeg.shape[0] == 30
    assert np.abs(eeg.sum(axis=0)).max() < 1e-9  # average reference
    tsv = pd.read_csv(fif.with_name(fif.name.replace("_eeg.fif", "_channels.tsv")), sep="\t")
    assert tsv.loc[tsv.interpolated, "name"].tolist() == ["F8"]
    qa = json.loads(fif.with_name(fif.name.replace("_eeg.fif", "_qa.json")).read_text("utf-8"))
    levels = {c["id"]: c["level"] for c in qa["checks"]}
    assert levels["REREF_APPLIED"] == "pass" and levels["PREPROC_BADS_FRACTION"] == "pass"
    assert qa["reference_free"] is True
    # Cached on a second run.
    assert Runner(cfg, project).run(subjects=["015"])[0].status == "skipped"


def test_sub015_reference_free_requires_reref(project: Path, tmp_path: Path) -> None:
    data = load_config(CONFIG).model_dump(mode="json")
    data["reference"]["scheme"] = "none"
    cfg = parse_config(data)
    root = tmp_path / "p"
    root.mkdir()
    (root / "rawdata").symlink_to(project / "rawdata")
    [out] = Runner(cfg, root).run(subjects=["015"])
    assert out.status == "failed" and "reference-free" in out.message
