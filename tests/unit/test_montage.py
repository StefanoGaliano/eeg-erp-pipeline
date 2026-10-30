from pathlib import Path

import mne
import numpy as np
import pytest

from eegunify.channels.montage import apply_montage
from eegunify.config.schema import MontageSection


def _raw(names: list[str]) -> mne.io.RawArray:
    info = mne.create_info(names, 100.0, "eeg")
    return mne.io.RawArray(np.zeros((len(names), 100)), info, verbose="error")


def test_standard_montage_sets_positions() -> None:
    raw = _raw(["Fp1", "Oz", "PO8"])
    missing = apply_montage(raw, MontageSection(), Path("."), "01")
    assert missing == []
    assert raw.get_montage() is not None


def test_standard_montage_reports_unknown_names(caplog: pytest.LogCaptureFixture) -> None:
    raw = _raw(["Fp1", "Weird1"])
    assert apply_montage(raw, MontageSection(), Path("."), "01") == ["Weird1"]
    assert "without positions" in caplog.text


def test_on_missing_raise() -> None:
    with pytest.raises(ValueError, match="Weird1"):
        apply_montage(_raw(["Fp1", "Weird1"]), MontageSection(on_missing="raise"), Path("."), "01")


@pytest.mark.filterwarnings("ignore:Fiducial point nasion")
def test_montage_file_per_subject(tmp_path: Path) -> None:
    # .sfp: "name x y z" per line; {subject} is filled per recording.
    (tmp_path / "sub-07.sfp").write_text("O1 -0.03 -0.1 0.0\nO2 0.03 -0.1 0.0\nOz 0 -0.11 0.0\n")
    raw = _raw(["O1", "O2", "Oz"])
    cfg = MontageSection(source="file", file="sub-{subject}.sfp")
    assert apply_montage(raw, cfg, tmp_path, "07") == []
    # MNE rescales .sfp files to a standard head size, so check geometry, not coordinates.
    pos = raw.get_montage().get_positions()["ch_pos"]
    assert pos["O1"][0] < 0 < pos["O2"][0]
    assert abs(pos["Oz"][0]) < 1e-9


def test_embedded_without_positions() -> None:
    raw = _raw(["Fp1"])
    with pytest.raises(ValueError, match="no positions"):
        apply_montage(raw, MontageSection(source="embedded", on_missing="raise"), Path("."), "01")


def test_none_skips() -> None:
    assert apply_montage(_raw(["Weird1"]), MontageSection(source="none"), Path("."), "01") == [
        "Weird1"
    ]
