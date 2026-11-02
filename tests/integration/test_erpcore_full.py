"""M-E6b: ERP CORE N170 on all 40 subjects (docs/BLUEPRINT_PHASES.md, OQ1 and section 14).

``code/configs/erpcore_n170_full.yaml`` (the N170 recipe, automatic bad channels) runs through
every stage, then the group stage compares with ERP CORE's published per-subject values.
Acceptance: the face - car mean amplitude at PO8 averaged over the compared subjects (those
both ERP CORE and eegunify kept) within the mean tolerance (±0.3 µV), and d_z with the same
sign as ERP CORE's over the same subjects. Per-subject deviations are printed (run with ``-s``)
and recorded in docs/validation_erp.md, not asserted.

Marker ``realdata_full``: never selected by default or by ``-m realdata``. The data are not
downloaded here; fetch them first (about 3.9 GB):
``eegunify fetch erpcore-full -c N170 --dest $EEGUNIFY_DATA/erpcore``.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

from eegunify.config.loader import load_config
from eegunify.erp import paths
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert

pytestmark = [pytest.mark.realdata_full, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
CONFIG = PROJECT / "code" / "configs" / "erpcore_n170_full.yaml"
MEAN_TOL_UV = 0.3


@pytest.fixture(scope="module")
def full_run(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    cfg = load_config(CONFIG)
    data = data_cache / "erpcore"
    missing = [
        s
        for s in cfg.recordings.subjects
        if not (data / "erpcore_full" / "N170" / cfg.recordings.path_for(s)).is_file()
    ]
    if missing:
        pytest.skip(f"{len(missing)} ERP CORE subjects not fetched (eegunify fetch erpcore-full)")
    root = tmp_path_factory.mktemp("erpcore_full")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = convert(cfg, project_root=root, qa=False)
        failed_convert = {r.subject: r.error for r in results if r.error}
        assert not failed_convert, failed_convert
        outs = Runner(cfg, root).run()
    group = next(o for o in outs if o.item.stage == "group")
    assert group.status == "ok", group.message
    excluded = {
        str(o.item.subject): o.message for o in outs if o.status == "failed" and o.item.subject
    }
    gdir = paths.group_dir(root, cfg)
    table = pd.read_csv(gdir / "task-N170_erpcore-comparison.tsv", sep="\t", dtype={"subject": str})
    stats = pd.read_csv(gdir / "task-N170_stats.tsv", sep="\t")
    print(f"\n[M-E6b] excluded by eegunify: {len(excluded)}")
    for sub, why in sorted(excluded.items()):
        print(f"  {sub}: {why[:140]}")
    rows = table[(table["wave"] == "face_minus_car") & (table["measure"] == "mean_amplitude")]
    print(rows.drop(columns=["note"]).to_string(index=False))
    print(table[table["subject"].isin(["mean", "d_z"])].to_string(index=False))
    print(stats.to_string(index=False))
    return table


def _row(table: pd.DataFrame, subject: str) -> pd.Series:
    sel = table[
        (table["subject"] == subject)
        & (table["wave"] == "face_minus_car")
        & (table["measure"] == "mean_amplitude")
    ]
    assert len(sel) == 1, sel
    return sel.iloc[0]


def test_grand_mean_within_tolerance(full_run: pd.DataFrame) -> None:
    mean = _row(full_run, "mean")
    assert abs(mean["ours"] - mean["theirs"]) <= MEAN_TOL_UV, mean.to_dict()


def test_d_z_same_sign(full_run: pd.DataFrame) -> None:
    dz = _row(full_run, "d_z")
    assert dz["theirs"] < 0 and dz["ours"] < 0, dz.to_dict()  # faces more negative than cars
