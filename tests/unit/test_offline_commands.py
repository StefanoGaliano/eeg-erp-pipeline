"""CLI commands, export, the validation harness and OpenNeuro fetching, without network."""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import mne
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from eegunify import datasets
from eegunify.cli import app
from eegunify.config.loader import load_config
from eegunify.export import export_eeglab
from eegunify.pipeline import convert
from eegunify.validation import Outcome, validate_entry, write_summary

runner = CliRunner()
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


# ------------------------------------------------------------------ export
def test_export_eeglab_roundtrip(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(synthetic_files["brainvision"], "brainvision")
    cfg = load_config(cfg_path)
    with pytest.raises(FileNotFoundError, match="convert"):
        export_eeglab(cfg, "01", root)
    assert convert(cfg, project_root=root)[0].error is None
    out = export_eeglab(cfg, "01", root)
    raw = mne.io.read_raw_eeglab(out, verbose="error")
    assert len(raw.annotations) == 100
    assert set(raw.annotations.description) == {"stimulus/face", "stimulus/car"}
    with pytest.raises(FileExistsError):
        export_eeglab(cfg, "01", root)


def test_export_cli(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg_path = make_project(synthetic_files["edf"], "edf")
    args = ["export", str(cfg_path), "--root", str(root)]
    assert runner.invoke(app, args).exit_code == 1  # not converted yet
    assert runner.invoke(app, ["convert", str(cfg_path), "--root", str(root)]).exit_code == 0
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, [*args, "--to", "fieldtrip"]).exit_code == 2


# ------------------------------------------------------------------ CLI misc
def test_fetch_cli_rejects_bad_arguments() -> None:
    assert runner.invoke(app, ["fetch", "erpcore", "--component", "P300"]).exit_code == 2
    assert runner.invoke(app, ["fetch", "erpcore", "-s", "001"]).exit_code == 2


def test_inspect_canonical_error_exit_code(tmp_path: Path, truth) -> None:  # type: ignore[no-untyped-def]
    from tests.synthetic.generate import to_raw

    raw = to_raw(truth)
    raw._data *= 1e6
    out = tmp_path / "v.vhdr"
    mne.export.export_raw(out, raw, fmt="brainvision", verbose="error")
    res = runner.invoke(app, ["inspect", str(out)])
    assert res.exit_code == 1 and "units.declared" in res.output


def test_config_schema_markdown_to_file(tmp_path: Path) -> None:
    out = tmp_path / "ref.md"
    res = runner.invoke(app, ["config", "schema", "--markdown", "--out", str(out)])
    assert res.exit_code == 0 and out.read_text(encoding="utf-8").startswith("# Config reference")


def test_validate_datasets_dry_run(tmp_path: Path) -> None:
    reg = tmp_path / "reg.yaml"
    reg.write_text(
        yaml.safe_dump({"datasets": [{"id": "x", "source": "zenodo", "format": "xdf"}]}),
        encoding="utf-8",
    )
    res = runner.invoke(app, ["validate-datasets", str(reg), "--dry-run"])
    assert res.exit_code == 0 and "zenodo" in res.output


# ------------------------------------------------------------------ validation harness
def test_harness_end_to_end_with_fake_fetch(
    tmp_path: Path, synthetic_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(dataset, dest, **kwargs):  # type: ignore[no-untyped-def]
        target = dest / dataset / "sub-01" / "eeg"
        target.mkdir(parents=True, exist_ok=True)
        src = synthetic_files["bdf"]
        out = target / "sub-01_task-x_eeg.bdf"
        out.write_bytes(src.read_bytes())
        return "1.0.0", "01", [out]

    monkeypatch.setattr(datasets, "fetch_openneuro", fake_fetch)
    work = tmp_path / "work"
    ok = validate_entry({"id": "ds1", "source": "openneuro", "format": "bdf"}, work, 100)
    assert ok.status in ("PASS", "WARN"), ok.error
    assert (ok.detected_format, ok.n_eeg, ok.n_events) == ("bdf", 64, 100)
    skip = validate_entry({"id": "z", "source": "zenodo"}, work, 100)
    assert skip.status == "SKIP"
    tsv = write_summary([ok, skip], work)
    table = pd.read_csv(tsv, sep="\t", dtype=str, keep_default_na=False)
    assert table["id"].tolist() == ["ds1", "z"]
    assert "| ds1 | bdf |" in (work / "summary.md").read_text(encoding="utf-8")
    # A later partial run replaces rows instead of duplicating them.
    write_summary([Outcome("z", "zenodo", "xdf", status="SKIP")], work)
    assert len(pd.read_csv(tsv, sep="\t")) == 2


def test_harness_reports_unreadable_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(dataset, dest, **kwargs):  # type: ignore[no-untyped-def]
        out = dest / dataset / "sub-01" / "eeg" / "sub-01_task-x_eeg.set"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"not a mat file")  # like ds006979's 11-byte upload
        return "1.0.0", "01", [out]

    monkeypatch.setattr(datasets, "fetch_openneuro", fake_fetch)
    res = validate_entry({"id": "bad", "source": "openneuro"}, tmp_path, 100)
    assert res.status == "ERROR" and res.stage == "convert"


# ------------------------------------------------------------------ OpenNeuro fetch
class _Resp(io.BytesIO):
    status = 200

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


def test_fetch_openneuro_downloads_skips_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [
        ("participants.json", 2, "https://s3/dead"),  # optional, 404 (ds003645)
        ("sub-01/eeg/sub-01_task-x_eeg.bdf", 4, "https://s3/bdf"),
    ]
    monkeypatch.setattr(datasets, "openneuro_subject_files", lambda *a, **k: ("1.0", "01", files))
    calls: list[str] = []

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        calls.append(req.full_url)
        if req.full_url.endswith("dead"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return _Resp(b"DATA")

    monkeypatch.setattr(datasets.urllib.request, "urlopen", fake_urlopen)
    tag, sub, paths = datasets.fetch_openneuro("ds1", tmp_path)
    assert (tag, sub) == ("1.0", "01")
    assert [p.name for p in paths] == ["sub-01_task-x_eeg.bdf"]
    assert paths[0].read_bytes() == b"DATA"
    n = len(calls)
    assert calls.count("https://s3/dead") == 1  # a 404 is permanent: not retried
    datasets.fetch_openneuro("ds1", tmp_path)  # cached by manifest hash: only the dead link again
    assert len(calls) == n + 1
    with pytest.raises(datasets.TooLargeError):
        datasets.fetch_openneuro("ds1", tmp_path, max_bytes=3)


def test_subject_files_walks_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = {
        None: [
            {"filename": "sub-01", "directory": True, "id": "s1", "size": 0, "urls": []},
            {"filename": "README", "directory": False, "id": "r", "size": 1, "urls": ["u/r"]},
        ],
        "s1": [
            {"filename": "ses-a", "directory": True, "id": "ses", "size": 0, "urls": []},
            {"filename": "anat", "directory": True, "id": "anat", "size": 0, "urls": []},
        ],
        "ses": [{"filename": "eeg", "directory": True, "id": "e", "size": 0, "urls": []}],
        "e": [
            {
                "filename": "sub-01_ses-a_task-x_eeg.vhdr",
                "directory": False,
                "id": "v",
                "size": 5,
                "urls": ["u/v"],
            }
        ],
    }
    monkeypatch.setattr(datasets, "_list", lambda ds, tag, node: tree[node])
    _, sub, files = datasets.openneuro_subject_files("ds1", tag="1.0")
    assert sub == "01"
    assert [f[0] for f in files] == ["README", "sub-01/ses-a/eeg/sub-01_ses-a_task-x_eeg.vhdr"]
    with pytest.raises(RuntimeError, match="no sub-99"):
        datasets.openneuro_subject_files("ds1", "99", tag="1.0")
