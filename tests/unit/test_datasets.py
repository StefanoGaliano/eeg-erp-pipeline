"""Offline tests for the fetch helpers (network calls are not made)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from eegunify import datasets
from eegunify.datasets import Manifest, _erpcore_members, one_recording


def test_one_recording_keeps_companions_only() -> None:
    files = [
        (r, 1, "u")
        for r in (
            "dataset_description.json",
            "participants.tsv",
            "sub-01/eeg/sub-01_task-a_run-1_eeg.vhdr",
            "sub-01/eeg/sub-01_task-a_run-1_eeg.eeg",
            "sub-01/eeg/sub-01_task-a_run-1_eeg.vmrk",
            "sub-01/eeg/sub-01_task-a_run-1_events.tsv",
            "sub-01/eeg/sub-01_task-a_run-2_eeg.vhdr",
            "sub-01/eeg/sub-01_task-a_run-2_eeg.eeg",
            "sub-01/eeg/sub-01_task-b_eeg.bdf",
            "sub-01/eeg/sub-01_electrodes.tsv",
        )
    ]
    kept = {r for r, _, _ in one_recording(files)}
    assert kept == {
        "dataset_description.json",
        "participants.tsv",
        "sub-01/eeg/sub-01_task-a_run-1_eeg.vhdr",
        "sub-01/eeg/sub-01_task-a_run-1_eeg.eeg",
        "sub-01/eeg/sub-01_task-a_run-1_eeg.vmrk",
        "sub-01/eeg/sub-01_task-a_run-1_events.tsv",
        "sub-01/eeg/sub-01_electrodes.tsv",
    }


def test_erpcore_member_selection() -> None:
    names = [
        "ERP_CORE/",
        "ERP_CORE/README",
        "ERP_CORE/task-N170_events.json",
        "ERP_CORE/task-P3_events.json",
        "ERP_CORE/sub-015/ses-N170/eeg/sub-015_ses-N170_task-N170_eeg.set",
        "ERP_CORE/sub-015/ses-P3/eeg/sub-015_ses-P3_task-P3_eeg.set",
        "ERP_CORE/sub-016/ses-N170/eeg/sub-016_ses-N170_task-N170_eeg.set",
    ]
    assert _erpcore_members(names, "N170", ["015"]) == [
        "ERP_CORE/README",
        "ERP_CORE/task-N170_events.json",
        "ERP_CORE/sub-015/ses-N170/eeg/sub-015_ses-N170_task-N170_eeg.set",
    ]


def test_manifest_detects_changed_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    m = Manifest.load(tmp_path)
    m.record(tmp_path, "a.txt", "test")
    assert Manifest.load(tmp_path).is_current(tmp_path, "a.txt")
    (tmp_path / "a.txt").write_text("two", encoding="utf-8")
    assert not Manifest.load(tmp_path).is_current(tmp_path, "a.txt")


def test_range_file_reads_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPRangeFile + zipfile, with the HTTP layer replaced by an in-memory archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ERP_CORE/README", "hello")
    blob = buf.getvalue()

    class Resp:
        def __init__(self, data: bytes) -> None:
            self.data, self.status = data, 206

        def read(self) -> bytes:
            return self.data

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req, timeout: float = 0) -> Resp:  # type: ignore[no-untyped-def]
        start, end = map(int, req.headers["Range"].removeprefix("bytes=").split("-"))
        return Resp(blob[start : end + 1])

    monkeypatch.setattr(datasets.urllib.request, "urlopen", fake_urlopen)
    remote = datasets.HTTPRangeFile("http://x", len(blob))
    with zipfile.ZipFile(io.BufferedReader(remote)) as z:
        assert z.read("ERP_CORE/README") == b"hello"
    assert remote.requests >= 1


def test_erpcore_rejects_unknown_subject(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in the archive"):
        datasets.fetch_erpcore(tmp_path, "N170", ["001"])
    with pytest.raises(ValueError, match="unknown ERP CORE component"):
        datasets.fetch_erpcore(tmp_path, "P300", ["015"])
