from pathlib import Path

from typer.testing import CliRunner

from eegunify.cli import app

runner = CliRunner()


def test_inspect(synthetic_files: dict[str, Path]) -> None:
    res = runner.invoke(app, ["inspect", str(synthetic_files["bdf"])])
    assert res.exit_code == 0, res.output
    assert "bdf" in res.output and "CMS/DRL" in res.output


def test_inspect_unknown_format(tmp_path: Path) -> None:
    p = tmp_path / "x.xyz"
    p.write_text("hi", encoding="utf-8")
    assert runner.invoke(app, ["inspect", str(p)]).exit_code == 2


def test_config_validate_and_schema(
    tmp_path: Path, synthetic_files: dict[str, Path], make_project
) -> None:  # type: ignore[no-untyped-def]
    _, cfg = make_project(synthetic_files["edf"], "edf")
    assert runner.invoke(app, ["config", "validate", str(cfg)]).exit_code == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("dataset: {name: x}\n", encoding="utf-8")
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


def test_config_dump_json_resolved(tmp_path: Path) -> None:
    """Phase M: the JSON dump applies overrides, resolves components and hashes both."""
    import json as _json

    cfg = Path(__file__).parents[2] / "code" / "configs" / "erpcore_n170.yaml"
    out = tmp_path / "dump.json"
    res = runner.invoke(
        app, ["config", "dump", str(cfg), "--json", "--resolved", "-s", "015", "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["subject"] == "015"
    assert data["config"]["events"]["mapping"]["1-40"] == "stimulus/face"
    assert data["components"]["n170"]["key"] == "n170@erpcore"
    assert len(data["config_hash"]) == 64
    plain = runner.invoke(app, ["config", "dump", str(cfg)])
    assert plain.exit_code == 0
    assert _json.loads(plain.output)["components"] == {}
    assert _json.loads(plain.output)["config_hash"] != data["config_hash"]
