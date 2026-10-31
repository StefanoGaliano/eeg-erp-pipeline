"""M-E2 on real data: ERP CORE N170 sub-015 through preproc and ica.

``code/configs/erpcore_n170.yaml`` derives bipolar VEOG (Fp2 - VEOG_lower) and HEOG, so both
votes (ICLabel and EOG correlation) and ``ICA_BLINK_RESIDUAL`` run. Component indices depend on
the BLAS/OS, so the test asserts classes and counts, not indices (BLUEPRINT_PHASES section 18).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

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
    root = tmp_path_factory.mktemp("erpcore_ica")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(CONFIG, subjects=["015"], project_root=root)
    assert res.error is None, res.error
    return root


def test_sub015_ica_excludes_blinks(project: Path) -> None:
    cfg = load_config(CONFIG)
    outs = {o.item.stage: o for o in Runner(cfg, project).run(subjects=["015"])}
    assert outs["ica"].status == "ok", outs["ica"].message

    def out(desc: str, suffix: str, ext: str) -> Path:
        return paths.stage_path(
            project, "ica", cfg, "015", description=desc, suffix=suffix, extension=ext
        )

    tsv = pd.read_csv(out("ica", "components", ".tsv"), sep="\t")
    excluded = tsv[tsv["excluded"]]
    assert (excluded["iclabel"] == "eye blink").sum() >= 1
    assert not (excluded["iclabel"] == "brain").any()
    assert {"eog_z_VEOG", "eog_z_HEOG"} <= set(tsv.columns)
    assert out("clean", "eeg", ".fif").exists()
    qa = json.loads(out("ica", "qa", ".json").read_text(encoding="utf-8"))
    levels = {c["id"]: c["level"] for c in qa["checks"]}
    assert levels == {
        "ICA_EXCLUDED_COUNT": "pass",
        "ICA_BLINK_RESIDUAL": "pass",
        "ICA_BRAIN_REMOVED": "pass",
    }
    assert Runner(cfg, project).run(subjects=["015"])[1].status == "skipped"
