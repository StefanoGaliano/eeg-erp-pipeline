from pathlib import Path

from typer.testing import CliRunner

from eegunify.cli import app

runner = CliRunner()


def test_inspect(synthetic_files: dict[str, Path]) -> None:
    res = runner.invoke(app, ["inspect", str(synthetic_files["bdf"])])
    assert res.exit_code == 0, res.output
    assert "bdf" in res.output and "CMS/DRL" in res.output


def test_inspect_unknown_format(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hi")
    assert runner.invoke(app, ["inspect", str(p)]).exit_code == 2


def test_config_validate_and_schema(
    tmp_path: Path, synthetic_files: dict[str, Path], make_project
) -> None:  # type: ignore[no-untyped-def]
    _, cfg = make_project(synthetic_files["edf"], "edf")
    assert runner.invoke(app, ["config", "validate", str(cfg)]).exit_code == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("dataset: {name: x}\n")
    res = runner.invoke(app, ["config", "validate", str(bad)])
    assert res.exit_code == 2 and "dataset.source_root" in res.output
    res = runner.invoke(app, ["config", "schema"])
    assert res.exit_code == 0 and '"properties"' in res.output


def test_convert_cli(synthetic_files: dict[str, Path], make_project) -> None:  # type: ignore[no-untyped-def]
    root, cfg = make_project(synthetic_files["eeglab"], "eeglab")
    res = runner.invoke(app, ["convert", str(cfg), "--root", str(root)])
    assert res.exit_code == 0, res.output
    assert (root / "rawdata" / "sub-01" / "eeg" / "sub-01_task-faces_eeg.vhdr").exists()
    res = runner.invoke(app, ["convert", str(cfg), "--root", str(root)])
    assert res.exit_code == 1  # exists, no --overwrite
