"""EGI simple binary ``.raw``: continuous and single-segment files (readers/egi.py)."""

from __future__ import annotations

from pathlib import Path

import mne
import numpy as np
import pytest

from eegunify.pipeline import convert, read_recording
from eegunify.readers.egi import EgiSegmentedError
from tests.synthetic.generate import SFREQ, Truth, write_egi_raw


def _read(path: Path) -> tuple[np.ndarray, list[tuple[int, str]], list[str]]:
    rec, _ = read_recording(path)
    raw = rec.raw
    eeg = mne.pick_types(raw.info, eeg=True)
    table = rec.events.table
    events = list(zip(table["sample"], table["value"].astype(str), strict=True))
    return raw.get_data(picks=eeg) * 1e6, events, raw.ch_names


def test_single_segment_reads_like_continuous(tmp_path: Path, truth: Truth) -> None:
    cont = write_egi_raw(tmp_path / "cont", truth)
    seg = write_egi_raw(tmp_path / "seg", truth, segments=1)
    d_cont, ev_cont, names = _read(cont)
    d_seg, ev_seg, names_seg = _read(seg)
    np.testing.assert_array_equal(d_seg, d_cont)
    assert ev_seg == ev_cont and names_seg == names
    np.testing.assert_allclose(d_cont, truth.data_uV, atol=1e-3)  # float32
    faces = [s for s, v in ev_cont if v == "ev01"]
    np.testing.assert_array_equal(faces, truth.samples[truth.codes == 1])
    assert sum(v == "resp" for _, v in ev_cont) == len(faces)
    assert mne.io.read_raw_egi(cont, verbose="error").info["sfreq"] == SFREQ


def test_gaba_resp_track_is_not_a_channel(tmp_path: Path, truth: Truth) -> None:
    """GABA infant VEP (Zenodo 5172962): an event track named "resp" became a respiration
    channel (name rule) and was kept as data. Trigger channels must stay triggers."""
    rec, _ = read_recording(write_egi_raw(tmp_path / "gaba", truth, segments=1))
    assert "resp" not in rec.raw.ch_names
    assert set(rec.raw.get_channel_types()) == {"eeg"}
    assert "resp" in set(rec.events.table["value"].astype(str))


def test_single_segment_converts_with_qa(tmp_path: Path, truth: Truth, make_project) -> None:  # type: ignore[no-untyped-def]
    """GABA infant VEP: the temporary continuous copy is deleted after reading, and QA's crop
    checked raw.filenames, so convert failed with FileNotFoundError."""
    (tmp_path / "src").mkdir()
    src = write_egi_raw(tmp_path / "src" / "rec", truth, segments=1)
    root, cfg = make_project(
        src,
        "egi",
        {
            "events": {
                "mapping": {"ev01": "stimulus/face", "ev02": "stimulus/car", "resp": "response"}
            }
        },
    )
    [res] = convert(cfg, project_root=root)
    assert res.error is None, res.error
    assert res.qa_status is not None


def test_several_segments_refused(tmp_path: Path, truth: Truth) -> None:
    path = write_egi_raw(tmp_path / "epoched", truth, segments=2)
    with pytest.raises(EgiSegmentedError, match="2 segments"):
        read_recording(path)
