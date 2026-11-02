"""Phase M1: MATLAB ingestion parity (docs/BLUEPRINT_PHASES.md section 11).

The same config goes through ``eegunify convert`` (Python) and ``eegunify.convert``
(``matlab/+eegunify``, fed by ``eegunify config dump --json --resolved``) for three source
formats: ERP CORE N170 sub-015 (EEGLAB ``.set``), and synthetic BrainVision and BioSemi BDF
files (64 positional channels, Status with high bits set). The outputs must agree:

- ``events.tsv``: identical rows (sample exact, ``value`` and ``trial_type`` exact);
- ``channels.tsv``: identical names, types, status and original names;
- channel data: max |diff| < 1e-3 µV (both float32 on disk);
- the MATLAB ``_eeg.json`` carries the dump's ``config_hash``.

All cases run in one MATLAB session (EEGLAB start-up is slow). Needs MATLAB with EEGLAB and
FieldTrip, set up by ``EEGUNIFY_MATLAB_SETUP``; skipped otherwise, as in CI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pandas as pd
import pytest
import yaml

from eegunify.config.dump import dump_config
from eegunify.config.loader import load_config
from eegunify.datasets import fetch_erpcore
from eegunify.pipeline import convert
from tests.synthetic.generate import biosemi64_truth, make_truth, write_bdf, write_brainvision

pytestmark = [pytest.mark.realdata, pytest.mark.filterwarnings("ignore::RuntimeWarning")]
PROJECT = Path(__file__).parents[2]
DATA_TOL_UV = 1e-3


def _matlab() -> str | None:
    return os.environ.get("EEGUNIFY_MATLAB") or shutil.which("matlab")


#: case -> {"python": vhdr, "matlab": vhdr, "hash": config_hash}
Parity = dict[str, dict[str, Any]]


def _synthetic_project(root: Path, fmt: str) -> tuple[Path, str]:
    """A project with one synthetic subject; returns (config path, subject)."""
    src = root / "sourcedata" / "synth"
    src.mkdir(parents=True)
    if fmt == "brainvision":
        write_brainvision(src / "sub-01_task-faces", make_truth())
        ext = ".vhdr"
    else:
        write_bdf(src / "sub-01_task-faces", biosemi64_truth())
        ext = ".bdf"
    cfg = {
        "dataset": {"name": f"Synthetic {fmt}", "source_root": "sourcedata/synth", "task": "faces"},
        "recordings": {"pattern": "sub-{subject}_task-faces" + ext, "subjects": ["01"]},
        "reference": {"online": "Cz"},
        "channels": {"bads": {"sub-01": ["O1"]}},
        "events": {
            "mapping": {"1": "stimulus/face", "2": "stimulus/car"},
            "unmapped": "error",
            "onset_shift_ms": {"stimulus/face": 10},
        },
    }
    path = root / "code" / "configs" / "synth.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path, "01"


def _erpcore_project(root: Path, data_cache: Path) -> tuple[Path, str]:
    try:
        fetch_erpcore(data_cache / "erpcore", "N170", ["015"])
    except OSError as err:  # offline
        pytest.skip(f"cannot fetch ERP CORE: {err}")
    (root / "sourcedata").mkdir(parents=True)
    (root / "sourcedata" / "erpcore").symlink_to(data_cache / "erpcore")
    return PROJECT / "code" / "configs" / "erpcore_n170.yaml", "015"


CASES = ["eeglab_erpcore", "brainvision", "bdf"]


@pytest.fixture(scope="module")
def parity(data_cache: Path, tmp_path_factory: pytest.TempPathFactory) -> Parity:
    """Convert every case in Python and in MATLAB; returns per case the two vhdr paths."""
    if _matlab() is None:
        pytest.skip("MATLAB not available")
    if "EEGUNIFY_MATLAB_SETUP" not in os.environ:
        pytest.skip("EEGUNIFY_MATLAB_SETUP (toolbox setup script) not set")
    cases: Parity = {}
    calls = []
    for case in CASES:
        root = tmp_path_factory.mktemp(case)
        if case == "eeglab_erpcore":
            config, subject = _erpcore_project(root, data_cache)
        else:
            config, subject = _synthetic_project(root, case)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            [res] = convert(config, subjects=[subject], project_root=root, qa=False)
        assert res.error is None, res.error
        assert res.output is not None
        dump = dump_config(load_config(config), subject=subject, resolved=True, project_root=root)
        dump_path = root / "config_dump.json"
        dump_path.write_text(json.dumps(dump), encoding="utf-8")
        out = root / "matlab_bids"
        cases[case] = {
            "python": res.output,
            "matlab": out / res.output.relative_to(root / "rawdata"),
            "hash": dump["config_hash"],
        }
        calls.append(f"eegunify.convert('{dump_path}', 'root', '{root}', 'bids_root', '{out}');")
    setup = os.environ["EEGUNIFY_MATLAB_SETUP"]
    script = f"addpath('{PROJECT / 'matlab'}'); eegunify.setup('{setup}'); " + " ".join(calls)
    proc = subprocess.run(
        [_matlab(), "-batch", script],  # type: ignore[list-item]
        capture_output=True,
        text=True,
        timeout=1200,
        check=False,
        stdin=subprocess.DEVNULL,  # MATLAB can block on an inherited stdin
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    return cases


def _sidecar(vhdr: Path, suffix: str) -> pd.DataFrame:
    path = vhdr.with_name(vhdr.name.replace("_eeg.vhdr", f"_{suffix}.tsv"))
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)


@pytest.mark.parametrize("case", CASES)
def test_events_identical(parity: Parity, case: str) -> None:
    py, mat = (_sidecar(parity[case][k], "events") for k in ("python", "matlab"))
    assert len(py) > 0
    assert len(mat) == len(py)
    for col in ("sample", "value", "trial_type"):
        assert mat[col].tolist() == py[col].tolist(), col
    np.testing.assert_allclose(mat["onset"].astype(float), py["onset"].astype(float), atol=1e-9)
    np.testing.assert_allclose(
        mat["duration"].astype(float), py["duration"].astype(float), atol=1e-9
    )


@pytest.mark.parametrize("case", CASES)
def test_channels_identical(parity: Parity, case: str) -> None:
    py, mat = (_sidecar(parity[case][k], "channels") for k in ("python", "matlab"))
    for col in ("name", "type", "status", "original_name"):
        assert mat[col].tolist() == py[col].tolist(), col


@pytest.mark.parametrize("case", CASES)
def test_data_within_float32(parity: Parity, case: str) -> None:
    py, mat = (
        mne.io.read_raw_brainvision(parity[case][k], preload=True, verbose="error")
        for k in ("python", "matlab")
    )
    assert mat.info["sfreq"] == py.info["sfreq"]
    assert mat.n_times == py.n_times
    diff_uv = np.abs(mat.get_data() - py.get_data()).max() * 1e6
    assert diff_uv < DATA_TOL_UV, f"max |diff| {diff_uv:.3g} µV"


@pytest.mark.parametrize("case", CASES)
def test_matlab_provenance_has_config_hash(parity: Parity, case: str) -> None:
    vhdr = parity[case]["matlab"]
    meta = json.loads(vhdr.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["eegunify"]["implementation"] == "matlab"
    assert meta["eegunify"]["config_hash"] == parity[case]["hash"]
