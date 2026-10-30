"""ERP CORE N170, sub-015: real data end to end (blueprint M3).

Fetches ~80 MB from OSF on first run (cached in ``EEGUNIFY_DATA``). Two independent checks:

1. **Golden files** in ``tests/golden/erpcore_n170_sub-015/``: our own reviewed output, so any
   change in behaviour shows up as a diff (regenerate with ``pytest --update-golden``).
2. **ERP CORE's own events.tsv**: event codes must be identical and onsets agree, which
   validates the extraction against the dataset authors rather than against ourselves.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eegunify.datasets import fetch_erpcore
from eegunify.pipeline import convert

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]

PROJECT = Path(__file__).parents[2]
GOLDEN = PROJECT / "tests" / "golden" / "erpcore_n170_sub-015"
BASE = "sub-015_ses-N170_task-N170"
#: Keys whose values change between runs or machines; removed before comparing.
VOLATILE_EEG_JSON = {"version"}


@pytest.fixture(scope="module")
def converted(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", ["015"])
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    root = tmp_path_factory.mktemp("erpcore")
    (root / "sourcedata").mkdir()
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    [res] = convert(
        PROJECT / "code" / "configs" / "erpcore_n170.yaml", subjects=["015"], project_root=root
    )
    assert res.error is None, res.error
    assert res.qa_status == "PASS"
    return root


def _normalized(root: Path) -> dict[str, str]:
    eeg_dir = root / "rawdata" / "sub-015" / "ses-N170" / "eeg"
    sidecar = json.loads((eeg_dir / f"{BASE}_eeg.json").read_text(encoding="utf-8"))
    for key in VOLATILE_EEG_JSON:
        sidecar["eegunify"].pop(key, None)
    qa = json.loads(
        (root / "derivatives" / "eegunify-qa" / "sub-015" / f"{BASE}_qa.json").read_text(
            encoding="utf-8"
        )
    )
    events = pd.read_csv(eeg_dir / f"{BASE}_events.tsv", sep="\t")
    return {
        "channels.tsv": (eeg_dir / f"{BASE}_channels.tsv").read_text(encoding="utf-8"),
        "events_head.tsv": events.head(50).to_csv(sep="\t", index=False),
        "event_counts.json": json.dumps(
            events["trial_type"].value_counts().sort_index().to_dict(), indent=1
        )
        + "\n",
        "eeg.json": json.dumps(sidecar, indent=1, sort_keys=True) + "\n",
        "qa_checks.json": json.dumps(
            {c["id"]: c["level"] for c in qa["checks"]}, indent=1, sort_keys=True
        )
        + "\n",
    }


def test_matches_golden(converted: Path, request: pytest.FixtureRequest) -> None:
    current = _normalized(converted)
    if request.config.getoption("--update-golden"):
        shutil.rmtree(GOLDEN, ignore_errors=True)
        GOLDEN.mkdir(parents=True)
        for name, text in current.items():
            (GOLDEN / name).write_text(text, encoding="utf-8")
        pytest.skip("golden files updated")
    for name, text in current.items():
        assert text == (GOLDEN / name).read_text(encoding="utf-8"), f"{name} differs from golden"


def test_events_match_erpcore_authors(converted: Path, data_cache: Path) -> None:
    ours = pd.read_csv(
        converted / "rawdata" / "sub-015" / "ses-N170" / "eeg" / f"{BASE}_events.tsv", sep="\t"
    )
    theirs = pd.read_csv(
        data_cache / "erpcore" / "ERP_CORE" / "sub-015" / "ses-N170" / "eeg" / f"{BASE}_events.tsv",
        sep="\t",
    )
    assert ours["value"].astype(int).tolist() == theirs["value"].astype(int).tolist()
    # Stimuli carry the 26 ms monitor delay (events.onset_shift_ms); responses are untouched.
    stim = ours["trial_type"].str.startswith("stimulus/")
    expected = theirs["onset"] + np.where(stim, 0.026, 0.0)
    assert (ours["onset"] - expected).abs().max() < 1e-3  # theirs rounded to 0.1 ms
    # ERP CORE writes EEGLAB's 1-based latency in `sample`; BIDS defines it 0-based.
    resp = ~stim
    assert (theirs["sample"][resp] - ours["sample"][resp]).eq(1).all()
