"""M-E3 on real data: ERP CORE N170 sub-015 through preproc, ica and epochs.

Rejection uses the blueprint defaults (ERPLAB semantics); ERP CORE's per-subject thresholds
come in M-E6a. The assertions are the facts that don't depend on ICA indices: every stimulus
event is logged with its rawdata code, rejection stays small, and the counts add up.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import mne
import pandas as pd
import pytest

from eegunify.config.loader import load_config
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
    root = tmp_path_factory.mktemp("erpcore_epochs")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(CONFIG, subjects=["015"], project_root=root)
    assert res.error is None, res.error
    return root


def test_sub015_epochs(project: Path) -> None:
    cfg = load_config(CONFIG)
    outs = {o.item.stage: o for o in Runner(cfg, project).run(subjects=["015"])}
    assert outs["epochs"].status == "ok", outs["epochs"].message

    def out(desc: str, suffix: str, ext: str) -> Path:
        return paths.stage_path(
            project, "epochs", cfg, "015", description=desc, suffix=suffix, extension=ext
        )

    log = pd.read_csv(out("stim", "log", ".tsv"), sep="\t")
    expected = {k: v for k, v in cfg.events.expected_counts.items() if k.startswith("stimulus/")}
    assert log.groupby("trial_type").size().to_dict() == expected
    assert log["code"].between(1, 180).all()  # rawdata codes, matched by onset
    assert (~log["kept"]).mean() < 0.10
    epochs = mne.read_epochs(out("stim", "epo", ".fif"), verbose=False)
    assert len(epochs) == log["kept"].sum()
    assert epochs.info["sfreq"] == 256
