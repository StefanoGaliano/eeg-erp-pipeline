from pathlib import Path

from typer.testing import CliRunner

from eegunify.cli import app

runner = CliRunner()
CFG = Path(__file__).parents[3] / "code" / "configs" / "erpcore_n170.yaml"


def test_components_table_and_filters() -> None:
    res = runner.invoke(app, ["erp", "components", "--preset", "iscev"], env={"COLUMNS": "200"})
    assert res.exit_code == 0, res.output
    assert "vep_p100@iscev" in res.output and "n170@erpcore" not in res.output
    assert runner.invoke(app, ["erp", "components", "--preset", "nope"]).exit_code == 2


def test_components_show() -> None:
    res = runner.invoke(app, ["erp", "components", "--show", "n170", "--preset", "erpcore"])
    assert res.exit_code == 0, res.output
    assert "n170@erpcore:" in res.output and "Kappenman" in res.output
    assert "n170@classic" not in res.output
    assert runner.invoke(app, ["erp", "components", "--show", "n999"]).exit_code == 2


def test_status_without_stages(tmp_path: Path) -> None:
    res = runner.invoke(app, ["erp", "status", str(CFG), "--root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "no ERP stages" in res.output
    assert runner.invoke(app, ["erp", "status", str(tmp_path / "x.yaml")]).exit_code == 2
