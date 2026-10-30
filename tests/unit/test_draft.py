from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from eegunify.cli import app
from eegunify.config.draft import (
    draft_config,
    event_code_table,
    mapping_from_rows,
    subject_pattern,
)
from eegunify.config.loader import parse_config
from eegunify.pipeline import read_recording


@pytest.mark.parametrize(
    ("path", "pattern", "subject"),
    [
        (
            "sub-001/eeg/sub-001_task-x_eeg.bdf",
            "sub-{subject}/eeg/sub-{subject}_task-x_eeg.bdf",
            "001",
        ),
        ("sub-01_task-faces.vhdr", "sub-{subject}_task-faces.vhdr", "01"),
        ("recording.edf", "recording.edf", None),
    ],
)
def test_subject_pattern(path: str, pattern: str, subject: str | None) -> None:
    assert subject_pattern(path) == (pattern, subject)


def test_mapping_from_rows_merges_ranges() -> None:
    rows = [{"code": c, "trial_type": "face"} for c in ("1", "2", "3")]
    rows += [
        {"code": "4", "trial_type": "car"},
        {"code": "5", "trial_type": "face"},
        {"code": "9", "trial_type": ""},
        {"code": "Comment/x", "trial_type": "note"},
    ]
    assert mapping_from_rows(rows) == {"1-3": "face", "4": "car", "5": "face", "Comment/x": "note"}


def test_event_code_table(synthetic_files: dict[str, Path]) -> None:
    rec, _ = read_recording(synthetic_files["brainvision"])
    table = event_code_table(rec)
    assert table["code"].tolist() == ["1", "2"]
    assert table["n"].tolist() == [50, 50]


def test_draft_is_valid_and_points_at_file(
    tmp_path: Path, synthetic_files: dict[str, Path], truth64
) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "sourcedata" / "study" / "sub-007" / "eeg"
    src.mkdir(parents=True)
    target = src / "sub-007_task-faces_eeg.bdf"
    target.write_bytes(synthetic_files["bdf"].read_bytes())
    rec, _ = read_recording(target)
    cfg = draft_config(rec, target, tmp_path)
    parsed = parse_config(cfg)
    assert parsed.dataset.source_root == "sourcedata/study"
    assert parsed.recordings.pattern == "sub-{subject}/eeg/sub-{subject}_task-faces_eeg.bdf"
    assert parsed.recordings.subjects == ["007"]
    assert parsed.dataset.task == "faces"
    assert parsed.reference.online.startswith("CMS/DRL")
    assert parsed.montage.standard_name == "biosemi64"
    assert parsed.events.mapping == {"1": "event/1", "2": "event/2"}


def test_config_init_cli(tmp_path: Path, synthetic_files: dict[str, Path]) -> None:
    out = tmp_path / "x.yaml"
    runner = CliRunner()
    res = runner.invoke(app, ["config", "init", str(synthetic_files["edf"]), "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert parse_config(yaml.safe_load(out.read_text(encoding="utf-8")))
    assert (
        runner.invoke(
            app, ["config", "init", str(synthetic_files["edf"]), "--out", str(out)]
        ).exit_code
        == 2
    )  # refuses to overwrite


@pytest.mark.parametrize(
    ("rel", "source_root", "pattern", "subjects"),
    [
        (
            "sourcedata/ds1/sub-01_task-1_eeg.vhdr",
            "sourcedata/ds1",
            "sub-{subject}_task-1_eeg.vhdr",
            ["01"],
        ),
        ("sourcedata/lab/P07.bdf", "sourcedata/lab", "{subject}.bdf", ["P07"]),
    ],
)
def test_draft_paths_without_reading(
    tmp_path: Path, rel: str, source_root: str, pattern: str, subjects: list[str]
) -> None:
    """Drafting from location alone must give a valid config (unreadable files, ds005274)."""
    from eegunify.config.draft import draft_paths

    cfg = draft_paths(tmp_path / rel, tmp_path)
    assert cfg["dataset"]["source_root"] == source_root
    assert cfg["recordings"] == {"pattern": pattern, "subjects": subjects}
    parse_config(cfg)


def test_draft_paths_keeps_symlinked_location(tmp_path: Path) -> None:
    """A dataset symlinked into sourcedata/ keeps its in-project path (not the link target)."""
    from eegunify.config.draft import draft_paths

    external = tmp_path / "drive" / "ds9"
    external.mkdir(parents=True)
    (external / "sub-02_task-a_eeg.bdf").write_bytes(b"")
    (tmp_path / "proj" / "sourcedata").mkdir(parents=True)
    (tmp_path / "proj" / "sourcedata" / "ds9").symlink_to(external)
    cfg = draft_paths(
        tmp_path / "proj" / "sourcedata" / "ds9" / "sub-02_task-a_eeg.bdf", tmp_path / "proj"
    )
    assert cfg["dataset"]["source_root"] == "sourcedata/ds9"
    assert cfg["dataset"]["name"] == "ds9"
