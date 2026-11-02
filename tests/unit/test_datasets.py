"""Offline tests for the fetch helpers (network calls are not made)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
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


def test_retry_retries_403_and_429_but_not_other_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSF answers bursts with 403 for public files: retried; a 404 is permanent."""
    import urllib.error

    monkeypatch.setattr(datasets.time, "sleep", lambda s: None)

    def flaky(codes: list[int]) -> Callable[[], str]:
        def call() -> str:
            if codes:
                raise urllib.error.HTTPError("u", codes.pop(0), "x", {}, None)  # type: ignore[arg-type]
            return "ok"

        return call

    assert datasets._retry(flaky([403, 429, 403]), "f") == "ok"
    with pytest.raises(urllib.error.HTTPError):
        datasets._retry(flaky([404]), "f")
    with pytest.raises(urllib.error.HTTPError):  # gives up after the attempts
        datasets._retry(flaky([403] * 9), "f", attempts=3)


def test_fetch_zenodo_checks_md5_and_skips_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GABA infant VEP files (Zenodo 5172962): MD5 from the record, then the manifest."""
    import hashlib
    import json

    blobs = {"a.raw": b"x" * 10, "notes.csv": b"y"}
    record = {
        "files": [
            {
                "key": k,
                "size": len(v),
                "checksum": "md5:" + hashlib.md5(v).hexdigest(),
                "links": {"self": f"http://z/{k}"},
            }
            for k, v in blobs.items()
        ]
    }
    calls: list[str] = []

    class Resp(io.BytesIO):
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req, timeout: float = 0) -> Resp:  # type: ignore[no-untyped-def]
        url = req.full_url
        calls.append(url)
        if url.startswith("https://zenodo.org/api/records/"):
            return Resp(json.dumps(record).encode())
        return Resp(blobs[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(datasets.urllib.request, "urlopen", fake_urlopen)
    [path] = datasets.fetch_zenodo("1", tmp_path, suffixes=[".raw"])
    assert path.read_bytes() == blobs["a.raw"] and path.parent.name == "zenodo1"
    n = len(calls)
    datasets.fetch_zenodo("1", tmp_path, files=["a.raw"])
    assert len(calls) == n + 1  # metadata only: the file is current
    record["files"][0]["checksum"] = "md5:0"
    path.unlink()
    with pytest.raises(ConnectionError, match="MD5"):
        datasets.fetch_zenodo("1", tmp_path, files=["a.raw"])
    with pytest.raises(ValueError, match="no files"):
        datasets.fetch_zenodo("1", tmp_path, files=["b.raw"])
