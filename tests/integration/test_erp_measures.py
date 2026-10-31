"""M-E4 on real data: ERP CORE N170 sub-015 measured by the erp stage.

1. The preview recipe reproduces the F6 golden value (-2.24 µV ± 0.05, face - car at PO8,
   110-150 ms), which the v0.1.0 preview script printed.
2. The ERP CORE recipe runs end to end and the N170 difference is negative at PO8 (the value
   itself is compared with ERP CORE's published one in M-E6a).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pandas as pd
import pytest

from eegunify.config.loader import load_config
from eegunify.config.schema import Config
from eegunify.datasets import fetch_erpcore
from eegunify.erp import paths
from eegunify.erp.recipes import apply_recipe
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
CONFIG = Path(__file__).parents[2] / "code" / "configs" / "erpcore_n170.yaml"
GOLDEN = (
    Path(__file__).parents[1] / "golden" / "erpcore_n170_sub-015" / "erp" / "preview_recipe.json"
)


@pytest.fixture(scope="module")
def project(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", ["015"])
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erpcore_measures")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(CONFIG, subjects=["015"], project_root=root)
    assert res.error is None, res.error
    return root


def _primary_mean(root: Path, cfg: Config) -> float:
    tsv = paths.stage_path(
        root, "erp", cfg, "015", description="stim", suffix="measures", extension=".tsv"
    )
    m = pd.read_csv(tsv, sep="\t")
    row = m.query("wave == 'face_minus_car' and roi == 'primary' and measure == 'mean_amplitude'")
    return float(row["value"].iloc[0])


def test_preview_recipe_golden(project: Path, tmp_path: Path) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cfg = apply_recipe(load_config(CONFIG), "preview")
    root = tmp_path / "preview"
    root.mkdir()
    (root / "rawdata").symlink_to(project / "rawdata")
    outs = {o.item.stage: o for o in Runner(cfg, root).run(subjects=["015"])}
    assert outs["erp"].status == "ok", outs["erp"].message
    value = _primary_mean(root, cfg)
    assert value == pytest.approx(golden["value_uv"], abs=golden["tolerance_uv"])


def test_erpcore_recipe_measures(project: Path) -> None:
    cfg = load_config(CONFIG)
    outs = {o.item.stage: o for o in Runner(cfg, project).run(subjects=["015"])}
    assert outs["erp"].status == "ok", outs["erp"].message
    assert _primary_mean(project, cfg) < 0
