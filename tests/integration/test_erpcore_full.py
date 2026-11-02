"""M-E6b: ERP CORE N170 on all 40 subjects (docs/BLUEPRINT_PHASES.md, OQ1 and section 14).

``code/configs/erpcore_n170_full.yaml`` runs through every stage twice: the default recipe
(automatic bad channels, ICA + ICLabel, our rejection) and ``erpcore-exact`` (ERP CORE's own
per-subject interpolation, ICA weights and rejection settings, ``N170_exact.json``). The group
stage compares each with ERP CORE's published per-subject values. Acceptance, per recipe: the
face - car mean amplitude at PO8 averaged over the compared subjects (those both ERP CORE and
eegunify kept) within the mean tolerance (±0.3 µV), and d_z with the same sign as ERP CORE's
over the same subjects. Per-subject deviations are printed (run with ``-s``; ``EEGUNIFY_KEEP=1``
copies the tables to ``derivatives/validation/``) and recorded in docs/validation_erp.md.

Marker ``realdata_full``: never selected by default or by ``-m realdata``. The data are not
downloaded here; fetch them first (about 3.9 GB, plus the parameter files):
``eegunify fetch erpcore-full -c N170 --dest $EEGUNIFY_DATA/erpcore`` and
``eegunify fetch erpcore-values -c N170 --dest $EEGUNIFY_DATA/erpcore``.
"""

from __future__ import annotations

import json
import os
import shutil
import warnings
from pathlib import Path

import pandas as pd
import pytest

from eegunify.config.loader import load_config
from eegunify.config.schema import Config
from eegunify.erp import paths
from eegunify.erp.recipes import erpcore_exact
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert
from eegunify.reference_values import REFERENCE_DIR

pytestmark = [pytest.mark.realdata_full, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
CONFIG = PROJECT / "code" / "configs" / "erpcore_n170_full.yaml"
MEAN_TOL_UV = 0.3


@pytest.fixture(scope="module")
def converted(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
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
    failed = {r.subject: r.error for r in results if r.error}
    assert not failed, failed
    return root


def _run(cfg: Config, root: Path, tag: str) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        outs = Runner(cfg, root).run()
    group = next(o for o in outs if o.item.stage == "group")
    assert group.status == "ok", group.message
    excluded = {
        str(o.item.subject): o.message for o in outs if o.status == "failed" and o.item.subject
    }
    gdir = paths.group_dir(root, cfg)
    table = pd.read_csv(gdir / "task-N170_erpcore-comparison.tsv", sep="\t", dtype={"subject": str})
    stats = pd.read_csv(gdir / "task-N170_stats.tsv", sep="\t")
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(f"\n[M-E6b {tag}] subjects failing a stage: {len(excluded)}")
        for sub, why in sorted(excluded.items()):
            print(f"  {sub}: {why[:140]}")
        rows = table[(table["wave"] == "face_minus_car") & (table["measure"] == "mean_amplitude")]
        print(rows.drop(columns=["note"]).to_string(index=False))
        print(table[table["subject"].isin(["mean", "d_z"])].to_string(index=False))
        print(stats.to_string(index=False))
    if os.environ.get("EEGUNIFY_KEEP"):
        out = PROJECT / "derivatives" / "validation"
        out.mkdir(parents=True, exist_ok=True)
        for name in ("task-N170_erpcore-comparison.tsv", "task-N170_stats.tsv"):
            shutil.copy(gdir / name, out / name.replace(".tsv", f"_full_{tag}.tsv"))
    return table


@pytest.fixture(scope="module")
def default_run(converted: Path) -> pd.DataFrame:
    return _run(load_config(CONFIG), converted, "default")


@pytest.fixture(scope="module")
def exact_run(
    converted: Path, data_cache: Path, tmp_path_factory: pytest.TempPathFactory
) -> pd.DataFrame:
    base = load_config(CONFIG)
    weights = data_cache / "erpcore" / "erpcore_exact" / "N170"
    params = json.loads((REFERENCE_DIR / "N170_exact.json").read_text(encoding="utf-8"))
    missing = [
        s
        for s in base.recordings.subjects
        if not (weights / params["subjects"][s]["ica_weights"]).is_file()
    ]
    if missing:
        pytest.skip(f"ICA weights of {len(missing)} subjects not fetched (fetch erpcore-values)")
    tsv = next(
        p for p in paths.rawdata_files(converted, base, "001") if p.name.endswith("_channels.tsv")
    )
    ch = pd.read_csv(tsv, sep="\t", encoding="utf-8")
    recorded = list(ch.loc[ch["type"].isin(["EEG", "EOG"]), "name"])
    cfg = erpcore_exact(base, params, recorded, weights)
    root = tmp_path_factory.mktemp("erpcore_full_exact")
    (root / "rawdata").symlink_to(converted / "rawdata")
    return _run(cfg, root, "exact")


def _row(table: pd.DataFrame, subject: str) -> pd.Series:
    sel = table[
        (table["subject"] == subject)
        & (table["wave"] == "face_minus_car")
        & (table["measure"] == "mean_amplitude")
    ]
    assert len(sel) == 1, sel
    return sel.iloc[0]


@pytest.mark.parametrize("recipe", ["default_run", "exact_run"])
def test_grand_mean_within_tolerance(recipe: str, request: pytest.FixtureRequest) -> None:
    table = request.getfixturevalue(recipe)
    mean = _row(table, "mean")
    assert abs(mean["ours"] - mean["theirs"]) <= MEAN_TOL_UV, mean.to_dict()


@pytest.mark.parametrize("recipe", ["default_run", "exact_run"])
def test_d_z_same_sign(recipe: str, request: pytest.FixtureRequest) -> None:
    dz = _row(request.getfixturevalue(recipe), "d_z")
    assert dz["theirs"] < 0 and dz["ours"] < 0, dz.to_dict()  # faces more negative than cars
