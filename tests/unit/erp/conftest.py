"""ERP fixtures: a converted synthetic project (rawdata ready for the ERP stages)."""

from __future__ import annotations

import shutil
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from eegunify.config.loader import parse_config
from eegunify.config.schema import Config
from tests.synthetic.erp import (
    ErpTruth,
    erp_config,
    make_erp_recording,
    make_vep_recording,
    vep_config,
    write_erp_brainvision,
)


@pytest.fixture(scope="session")
def erp_truth() -> ErpTruth:
    return make_erp_recording(60.0)


def _convert(
    root: Path, truth: ErpTruth, cfg: dict[str, Any], stem: str = "sub-01_task-faces"
) -> None:
    from eegunify.pipeline import convert

    write_erp_brainvision(root / "sourcedata" / "synth", truth, stem)
    path = root / "code" / "configs" / "synth.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [res] = convert(path, project_root=root)
    assert res.error is None, res.error


@pytest.fixture(scope="session")
def erp_rawdata(tmp_path_factory: pytest.TempPathFactory, erp_truth: ErpTruth) -> Path:
    """Template project converted once per session (online reference Cz)."""
    root = tmp_path_factory.mktemp("erp_template")
    _convert(root, erp_truth, erp_config())
    return root


@pytest.fixture
def erp_project(tmp_path: Path, erp_rawdata: Path) -> Callable[..., tuple[Path, Config]]:
    """Fresh copy of the converted project plus a config with the given ERP sections."""

    def _make(**sections: dict[str, Any]) -> tuple[Path, Config]:
        root = tmp_path / "project"
        shutil.copytree(erp_rawdata, root, symlinks=True)
        return root, parse_config(erp_config(**sections))

    return _make


@pytest.fixture(scope="session")
def blink_truth() -> ErpTruth:
    """Blinks, no dead/noisy channel and no line noise: what the ICA tests need."""
    return make_erp_recording(60.0, blinks=True, dead=False, noisy=False, line_uV=0.0)


@pytest.fixture(scope="session")
def blink_rawdata(tmp_path_factory: pytest.TempPathFactory, blink_truth: ErpTruth) -> Path:
    root = tmp_path_factory.mktemp("erp_blinks")
    _convert(root, blink_truth, erp_config())
    return root


@pytest.fixture
def blink_project(tmp_path: Path, blink_rawdata: Path) -> Callable[..., tuple[Path, Config]]:
    """Like ``erp_project``, with the blink recording."""

    def _make(**sections: dict[str, Any]) -> tuple[Path, Config]:
        root = tmp_path / "project"
        shutil.copytree(blink_rawdata, root, symlinks=True)
        return root, parse_config(erp_config(**sections))

    return _make


@pytest.fixture(scope="session")
def vep_truth() -> ErpTruth:
    return make_vep_recording(120.0)


@pytest.fixture(scope="session")
def vep_rawdata(tmp_path_factory: pytest.TempPathFactory, vep_truth: ErpTruth) -> Path:
    """Synthetic pattern-reversal VEP project (BLUEPRINT_PHASES section 12), converted once."""
    root = tmp_path_factory.mktemp("vep")
    _convert(root, vep_truth, vep_config(), stem="sub-01_task-vep")
    return root
