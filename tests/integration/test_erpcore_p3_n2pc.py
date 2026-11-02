"""M-E6a: ERP CORE P3 and N2pc, sub-015..019, vs ERP CORE's published values (BLUEPRINT 8.2).

Each component's config (``code/configs/erpcore_p3.yaml``, ``erpcore_n2pc.yaml``) runs end to
end, group stage included, with two recipes (as N170 in ``test_erpcore_comparison.py``):

* ``default``: the config as is (picard + ICLabel, one rejection rule set for everyone);
* ``exact``: :func:`eegunify.erp.recipes.erpcore_exact`, ERP CORE's own per-subject choices.

The group stage writes ``task-<c>_erpcore-comparison.tsv``. Acceptance as for N170: per
compared subject, sign equal to ERP CORE's and ±0.5 µV on the contrast mean amplitude, ±0.3 µV
on the subject mean, and no subject excluded that ERP CORE kept. The default recipe's known
deviations are documented in docs/validation_erp.md, not encoded here: its tests report them
(``-s`` prints the tables; ``EEGUNIFY_KEEP`` copies them to ``derivatives/validation``).
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
from eegunify.datasets import ERPCORE_SUBJECTS, fetch_erpcore
from eegunify.erp import paths
from eegunify.erp.comparison import failures
from eegunify.erp.recipes import erpcore_exact
from eegunify.erp.runner import Runner
from eegunify.erpcore_exact import fetch_exact
from eegunify.pipeline import convert
from eegunify.reference_values import REFERENCE_DIR

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
SUBJECTS = list(ERPCORE_SUBJECTS)
CONTRAST = {"P3": "rare_minus_frequent", "N2pc": "contra_minus_ipsi"}
#: Default-recipe deviations documented in docs/validation_erp.md ("Documented deviations"):
#: subjects that fail a stage, and subjects outside tolerance. Exact sets: a new deviation, or
#: a documented one that disappears, fails the test so the docs get updated.
KNOWN_DEFAULT = {
    "P3": {"failed": {"016"}, "out_of_tolerance": {"019"}},
    "N2pc": {"failed": set(), "out_of_tolerance": {"019"}},
}


def _config(comp: str) -> Path:
    return PROJECT / "code" / "configs" / f"erpcore_{comp.lower()}.yaml"


@pytest.fixture(scope="module", params=["P3", "N2pc"])
def converted(
    request: pytest.FixtureRequest, data_cache: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[str, Path]:
    comp: str = request.param
    try:
        fetch_erpcore(data_cache / "erpcore", comp, SUBJECTS)
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE {comp}: {err}")
    root = tmp_path_factory.mktemp(f"erpcore_{comp}")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = convert(_config(comp), subjects=SUBJECTS, project_root=root)
    assert all(r.error is None for r in results), [r.error for r in results]
    return comp, root


def _exact(cfg: Config, comp: str, root: Path, data_cache: Path) -> Config:
    try:
        folder = fetch_exact(data_cache / "erpcore", comp)
    except OSError as err:
        pytest.skip(f"cannot fetch ERP CORE {comp} parameters: {err}")
    params = json.loads((REFERENCE_DIR / f"{comp}_exact.json").read_text(encoding="utf-8"))
    tsv = next(p for p in paths.rawdata_files(root, cfg, "015") if p.name.endswith("_channels.tsv"))
    ch = pd.read_csv(tsv, sep="\t", encoding="utf-8")
    recorded = list(ch.loc[ch["type"].isin(["EEG", "EOG"]), "name"])
    return erpcore_exact(cfg, params, recorded, folder)


def _run(comp: str, cfg: Config, root: Path, tag: str) -> tuple[pd.DataFrame, dict[str, str]]:
    outs = Runner(cfg, root).run(subjects=SUBJECTS)
    failed = {str(o.item.subject): o.message for o in outs if o.status == "failed"}
    group = next(o for o in outs if o.item.stage == "group")
    assert group.status == "ok", group.message
    gdir = paths.group_dir(root, cfg)
    table = pd.read_csv(
        gdir / f"task-{comp}_erpcore-comparison.tsv", sep="\t", dtype={"subject": str}
    )
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(f"\n[{comp} {tag}] failed subjects: {failed}")
        print(table.drop(columns=["note"]).to_string(index=False))
        print(pd.read_csv(gdir / f"task-{comp}_stats.tsv", sep="\t").to_string(index=False))
    if os.environ.get("EEGUNIFY_KEEP"):
        out = PROJECT / "derivatives" / "validation"
        out.mkdir(parents=True, exist_ok=True)
        for name in (f"task-{comp}_erpcore-comparison.tsv", f"task-{comp}_stats.tsv"):
            shutil.copy(gdir / name, out / name.replace(".tsv", f"_{tag}.tsv"))
    return table, failed


@pytest.fixture(scope="module")
def default_run(converted: tuple[str, Path]) -> tuple[str, pd.DataFrame, dict[str, str]]:
    comp, root = converted
    return comp, *_run(comp, load_config(_config(comp)), root, "default")


@pytest.fixture(scope="module")
def exact_run(
    converted: tuple[str, Path], data_cache: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[str, pd.DataFrame, dict[str, str]]:
    comp, converted_root = converted
    root = tmp_path_factory.mktemp(f"erpcore_{comp}_exact")
    (root / "rawdata").symlink_to(converted_root / "rawdata")
    cfg = _exact(load_config(_config(comp)), comp, converted_root, data_cache)
    return comp, *_run(comp, cfg, root, "exact")


def _check(
    comp: str,
    table: pd.DataFrame,
    failed: dict[str, str],
    known: dict[str, set[str]] | None = None,
) -> None:
    rows = table[(table["wave"] == CONTRAST[comp]) & (table["measure"] == "mean_amplitude")]
    known = known or {"failed": set(), "out_of_tolerance": set()}
    # ERP CORE excluded none of 015..019 for P3 or N2pc: every subject must be compared,
    # except the documented ones.
    assert set(failed) == known["failed"], failed
    bad = failures(rows)
    bad = bad[~bad["subject"].astype(str).isin(known["failed"])]  # missing_ours: same deviation
    assert set(bad["subject"].astype(str)) == known["out_of_tolerance"], bad.to_string()


def test_default_recipe(default_run: tuple[str, pd.DataFrame, dict[str, str]]) -> None:
    """The default recipe; its deviations are documented in docs/validation_erp.md."""
    comp = default_run[0]
    _check(*default_run, known=KNOWN_DEFAULT[comp])


def test_exact_recipe(exact_run: tuple[str, pd.DataFrame, dict[str, str]]) -> None:
    _check(*exact_run)
