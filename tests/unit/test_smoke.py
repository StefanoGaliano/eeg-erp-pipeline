"""M0 smoke tests: the package imports and the CLI runs."""

from typer.testing import CliRunner

import eegunify
from eegunify.cli import app


def test_version_string() -> None:
    assert isinstance(eegunify.__version__, str)
    assert eegunify.__version__ != "0.0.0"


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "canonical" in result.output


def test_cli_version_reports_mne() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "mne" in result.output
