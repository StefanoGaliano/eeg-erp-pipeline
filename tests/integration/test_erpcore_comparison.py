"""M-E6a: ERP CORE N170 sub-015..019 vs ERP CORE's published per-subject values (BLUEPRINT 8.2).

Two recipes run on the five subjects of the BIDS archive and are compared with
``src/eegunify/erp/reference/N170.tsv``:

* ``default``: ``code/configs/erpcore_n170.yaml`` (picard + ICLabel, one rejection rule set);
* ``exact``: :func:`eegunify.erp.recipes.erpcore_exact`, ERP CORE's own per-subject choices
  (their ICA weights and components, interpolation, rejection tests, 20 Hz peak low-pass;
  BLUEPRINT 8.2a). Its trial counts are also compared with ERP CORE's AR percentages.

Subjects ERP CORE excluded (sub-016) are run and reported but not checked. Tables are written
to ``derivatives/validation/erpcore_comparison_N170_<recipe>.tsv`` when ``EEGUNIFY_KEEP`` is
set, and printed with ``-s``; results are recorded in docs/validation_erp.md.
"""

from __future__ import annotations

import json
import os
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
from eegunify.reference_values import REFERENCE_DIR, read_tidy

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
CONFIG = PROJECT / "code" / "configs" / "erpcore_n170.yaml"
GOLDEN = REFERENCE_DIR
SUBJECTS = list(ERPCORE_SUBJECTS)
#: ERP CORE AR-percentage bin names -> our condition names (BDF_N170.txt bins 1-2).
AR_BINS = {"Faces - Correct": "face", "Cars - Correct": "car"}


@pytest.fixture(scope="module")
def converted(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", SUBJECTS)
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erpcore_converted")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = convert(CONFIG, subjects=SUBJECTS, project_root=root)
    assert all(r.error is None for r in results), [r.error for r in results]
    return root


def _exact_config(cfg: Config, root: Path, data_cache: Path) -> Config:
    try:
        folder = fetch_exact(data_cache / "erpcore", "N170")
    except OSError as err:
        pytest.skip(f"cannot fetch ERP CORE parameters: {err}")
    params = json.loads((GOLDEN / "N170_exact.json").read_text(encoding="utf-8"))
    tsv = next(p for p in paths.rawdata_files(root, cfg, "015") if p.name.endswith("_channels.tsv"))
    ch = pd.read_csv(tsv, sep="\t", encoding="utf-8")
    recorded = list(ch.loc[ch["type"].isin(["EEG", "EOG"]), "name"])
    return erpcore_exact(cfg, params, recorded, folder)


def _run(cfg: Config, root: Path, tag: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the ERP stages; return (comparison table, kept trials per subject x condition)."""
    outs = Runner(cfg, root).run(subjects=SUBJECTS)
    _, summary = read_tidy(GOLDEN, "N170")
    excluded_by_them = set(summary.loc[~summary["included"], "subject"])
    failed = {o.item.subject: o.message for o in outs if o.status == "failed"}
    # Our own QA may exclude a subject (EPOCHS_MIN_TRIALS), but only one ERP CORE excluded too;
    # excluding a subject they kept would be a deviation to document, not to pass silently.
    assert set(failed) <= excluded_by_them | {None}, failed
    group = next(o for o in outs if o.item.stage == "group")
    assert group.status == "ok", group.message
    # The group stage writes the comparison (group.reference_values: erpcore).
    gdir = paths.group_dir(root, cfg)
    table = pd.read_csv(gdir / "task-N170_erpcore-comparison.tsv", sep="\t", dtype={"subject": str})

    def stage_file(stage: str, s: str, desc: str, suffix: str, ext: str) -> Path:
        return paths.stage_path(root, stage, cfg, s, description=desc, suffix=suffix, extension=ext)

    kept = []
    for s in SUBJECTS:
        log = pd.read_csv(stage_file("epochs", s, "stim", "log", ".tsv"), sep="\t")
        for cond in AR_BINS.values():
            rows = log[log["trial_type"] == f"stimulus/{cond}"]
            candidates = rows[rows["reason"] != "NO_RESPONSE"]
            kept.append(
                {
                    "subject": s,
                    "condition": cond,
                    "candidates": len(candidates),
                    "kept": int(candidates["kept"].sum()),
                }
            )
    counts = pd.DataFrame(kept)
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(f"\n[{tag}]\n" + table.drop(columns=["note"]).to_string(index=False))
        print(counts.to_string(index=False))
    if os.environ.get("EEGUNIFY_KEEP"):
        out = PROJECT / "derivatives" / "validation" / f"erpcore_comparison_N170_{tag}.tsv"
        out.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(out, sep="\t", index=False, na_rep="n/a", encoding="utf-8")
        counts.to_csv(out.with_name(f"erpcore_trials_N170_{tag}.tsv"), sep="\t", index=False)
    return table, counts


@pytest.fixture(scope="module")
def default_run(converted: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _run(load_config(CONFIG), converted, "default")


@pytest.fixture(scope="module")
def exact_run(
    converted: Path, data_cache: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = tmp_path_factory.mktemp("erpcore_exact")
    (root / "rawdata").symlink_to(converted / "rawdata")
    cfg = _exact_config(load_config(CONFIG), converted, data_cache)
    return _run(cfg, root, "exact")


def _diff_rows(table: pd.DataFrame) -> pd.DataFrame:
    return table.query("wave == 'face_minus_car' and measure == 'mean_amplitude'")


def test_every_subject_is_listed(default_run: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    diff = _diff_rows(default_run[0])
    assert list(diff["subject"]) == [*SUBJECTS, "mean", "d_z"]
    assert diff.set_index("subject").loc["016", "status"] == "excluded_by_erpcore"


@pytest.mark.xfail(
    strict=True,
    reason="documented deviation (docs/validation_erp.md, M-E6a): with the automatic recipe "
    "sub-017 is 0.68 uV off; erpcore-exact (ERP CORE's own per-subject choices) is within 0.5",
)
def test_n170_mean_amplitude_matches_erpcore(
    default_run: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    """Headline acceptance (default recipe): sign equal to ERP CORE's, ±0.5 µV, mean ±0.3 µV."""
    rows = _diff_rows(default_run[0])
    assert failures(rows).empty, failures(rows).to_string()


def test_exact_recipe_matches_erpcore(exact_run: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """erpcore-exact: same checks on the face - car mean amplitude."""
    rows = _diff_rows(exact_run[0])
    assert failures(rows).empty, failures(rows).to_string()


def test_exact_recipe_trial_counts(exact_run: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """Kept trials per bin vs ERP CORE's ``<sub>_AR_Percentages_N170.csv`` (reported)."""
    params = json.loads((GOLDEN / "N170_exact.json").read_text(encoding="utf-8"))
    counts = exact_run[1].set_index(["subject", "condition"])
    rows = []
    for sub in SUBJECTS:
        ar = params["subjects"][sub]["ar_percentages"]
        for theirs_bin, cond in AR_BINS.items():
            theirs = ar[theirs_bin]
            ours = counts.loc[(sub, cond)]
            rows.append(
                {
                    "subject": sub,
                    "condition": cond,
                    "candidates_ours": int(ours["candidates"]),
                    "candidates_theirs": int(theirs["accepted"] + theirs["rejected"]),
                    "kept_ours": int(ours["kept"]),
                    "kept_theirs": int(theirs["accepted"]),
                }
            )
    table = pd.DataFrame(rows)
    print("\n" + table.to_string(index=False))
    # Same correct-response trials must be candidates (same events, same bin rule).
    assert (table["candidates_ours"] == table["candidates_theirs"]).all(), table.to_string()
