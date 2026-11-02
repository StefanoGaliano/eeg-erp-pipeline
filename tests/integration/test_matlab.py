"""MATLAB interop: matlab/load_canonical.m on the converted ERP CORE recording.

Needs MATLAB with EEGLAB (and FieldTrip or bva-io) set up by ``EEGUNIFY_MATLAB_SETUP`` (a .m
script, e.g. Practice/setup_eeg_toolboxes.m). Skipped when MATLAB is not available, as in CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.integration.test_erpcore import BASE, converted  # noqa: F401  (fixture)

pytestmark = pytest.mark.realdata
PROJECT = Path(__file__).parents[2]


def _matlab() -> str | None:
    return os.environ.get("EEGUNIFY_MATLAB") or shutil.which("matlab")


@pytest.mark.skipif(_matlab() is None, reason="MATLAB not available")
@pytest.mark.skipif(
    "EEGUNIFY_MATLAB_SETUP" not in os.environ,
    reason="EEGUNIFY_MATLAB_SETUP (toolbox setup script) not set",
)
def test_load_canonical_runtests(converted: Path) -> None:  # noqa: F811
    vhdr = converted / "rawdata" / "sub-015" / "ses-N170" / "eeg" / f"{BASE}_eeg.vhdr"
    setup = Path(os.environ["EEGUNIFY_MATLAB_SETUP"])
    script = (
        f"cd('{setup.parent}'); evalc('{setup.stem}'); "
        f"addpath('{PROJECT / 'matlab'}'); setenv('EEGUNIFY_TEST_VHDR', '{vhdr}'); "
        f"r = runtests('{PROJECT / 'matlab' / 'tests'}'); "
        # Assumption-filtered tests (e.g. bva-io missing) are Incomplete, not Failed.
        "disp(table(r)); assert(~any([r.Failed]), 'MATLAB tests failed');"
    )
    proc = subprocess.run(
        [_matlab(), "-batch", script],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        stdin=subprocess.DEVNULL,  # MATLAB can block on an inherited stdin
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
