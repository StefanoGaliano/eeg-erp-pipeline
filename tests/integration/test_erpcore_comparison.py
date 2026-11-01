"""M-E6a: ERP CORE N170 sub-015..019 vs ERP CORE's published per-subject values (BLUEPRINT 8.2).

Runs the ERP CORE recipe (``code/configs/erpcore_n170.yaml``) on the five subjects of the BIDS
archive and compares the primary-ROI measures with ``tests/golden/erpcore_reference/N170.tsv``.
Subjects ERP CORE excluded (sub-016) are run and reported but not checked. The table is
written to ``derivatives/validation/erpcore_comparison_N170.tsv`` when ``EEGUNIFY_KEEP`` is
set, and printed with ``-s``; results are recorded in docs/validation_erp.md.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pandas as pd
import pytest

from eegunify.config.loader import load_config
from eegunify.datasets import ERPCORE_SUBJECTS, fetch_erpcore
from eegunify.erp import paths
from eegunify.erp.comparison import comparison_table, failures
from eegunify.erp.runner import Runner
from eegunify.pipeline import convert
from eegunify.reference_values import read_tidy

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
CONFIG = PROJECT / "code" / "configs" / "erpcore_n170.yaml"
GOLDEN = PROJECT / "tests" / "golden" / "erpcore_reference"
SUBJECTS = list(ERPCORE_SUBJECTS)


@pytest.fixture(scope="module")
def comparison(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", SUBJECTS)
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erpcore_comparison")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = convert(CONFIG, subjects=SUBJECTS, project_root=root)
    assert all(r.error is None for r in results), [r.error for r in results]
    cfg = load_config(CONFIG)
    outs = Runner(cfg, root).run(subjects=SUBJECTS)
    published, summary = read_tidy(GOLDEN, "N170")
    excluded_by_them = set(summary.loc[~summary["included"], "subject"])
    failed = {o.item.subject: o.message for o in outs if o.status == "failed"}
    # Our own QA may exclude a subject (EPOCHS_MIN_TRIALS), but only one ERP CORE excluded too;
    # excluding a subject they kept would be a deviation to document, not to pass silently.
    assert set(failed) <= excluded_by_them, failed
    measured = [s for s in SUBJECTS if s not in failed]
    measures = pd.concat(
        pd.read_csv(
            paths.stage_path(
                root, "erp", cfg, s, description="stim", suffix="measures", extension=".tsv"
            ),
            sep="\t",
            dtype={"subject": str},
        )
        for s in measured
    )
    table = comparison_table("N170", measures, published, summary, SUBJECTS)
    for sub, why in failed.items():
        mask = table["subject"] == sub
        table.loc[mask, "note"] = table.loc[mask, "note"] + f"; excluded by eegunify too: {why}"
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print("\n" + table.to_string(index=False))
    if os.environ.get("EEGUNIFY_KEEP"):
        out = PROJECT / "derivatives" / "validation" / "erpcore_comparison_N170.tsv"
        out.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(out, sep="\t", index=False, na_rep="n/a", encoding="utf-8")
    return table


def test_every_subject_is_listed(comparison: pd.DataFrame) -> None:
    diff = comparison.query("wave == 'face_minus_car' and measure == 'mean_amplitude'")
    assert list(diff["subject"]) == [*SUBJECTS, "mean"]
    assert diff.set_index("subject").loc["016", "status"] == "excluded_by_erpcore"


def test_n170_mean_amplitude_matches_erpcore(comparison: pd.DataFrame) -> None:
    """Headline acceptance: face - car at PO8, sign equal to ERP CORE's, ±0.5 µV, mean ±0.3 µV."""
    rows = comparison.query("wave == 'face_minus_car' and measure == 'mean_amplitude'")
    assert failures(rows).empty, failures(rows).to_string()
