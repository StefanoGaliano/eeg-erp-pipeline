from pathlib import Path

import mne
import numpy as np
import pytest

from eegunify.detect import UnknownFormatError, detect_format
from eegunify.units import check_units


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("a.vhdr", "brainvision"),
        ("a.BDF", "bdf"),
        ("a.edf", "edf"),
        ("a.set", "eeglab"),
        ("a.xdf", "xdf"),
        ("a.gdf", "gdf"),
        ("a.cdt", "curry"),
    ],
)
def test_detect_by_extension(name: str, fmt: str) -> None:
    assert detect_format(name) == fmt


@pytest.mark.parametrize("name", ["a.eeg", "a.vmrk", "a.fdt", "a.edf.gz", "a.xyz"])
def test_detect_rejects(name: str) -> None:
    with pytest.raises(UnknownFormatError):
        detect_format(name)


@pytest.mark.parametrize(("magic", "fmt"), [(b"RIFF....", "ant"), (b"Version 3.0", "neuroscan")])
def test_cnt_magic(tmp_path: Path, magic: bytes, fmt: str) -> None:
    p = tmp_path / "a.cnt"
    p.write_bytes(magic)
    assert detect_format(p) == fmt


def _raw(scale_V: float) -> mne.io.RawArray:
    rng = np.random.default_rng(1)
    info = mne.create_info(["Fp1", "Cz", "Oz"], 250.0, "eeg")
    return mne.io.RawArray(rng.standard_normal((3, 2500)) * scale_V, info, verbose="error")


@pytest.mark.parametrize(
    ("scale", "plausible", "factor"),
    [(20e-6, True, 1.0), (20.0, False, 1e-6), (20e-3, False, 1e-3), (20e-12, False, 1e6)],
)
def test_check_units(scale: float, plausible: bool, factor: float) -> None:
    v = check_units(_raw(scale), (0.5, 500.0))
    assert v.plausible is plausible
    assert v.suggested_factor == factor
