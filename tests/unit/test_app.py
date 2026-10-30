"""Streamlit app tests (AppTest runs the script headless, no browser)."""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

import pytest
import yaml

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from eegunify.pipeline import convert

APP = str(Path(__file__).parents[2] / "src" / "eegunify" / "app" / "main.py")
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def _app(root: Path, **state: object) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["root"] = str(root)
    for key, val in state.items():
        at.session_state[key] = val
    return at.run()


@pytest.fixture
def project(synthetic_files: dict[str, Path], make_project) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    return make_project(synthetic_files["brainvision"], "brainvision")  # type: ignore[no-any-return]


def test_inspect_page(project: tuple[Path, Path]) -> None:
    root, _ = project
    at = _app(root)
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Format"] == "brainvision"
    assert metrics["Events"] == "100"
    configure = next(b for b in at.button if b.label.startswith("Configure"))
    configure.click().run()
    assert at.session_state["page"].endswith("Configure")
    assert at.session_state["file"] == "sourcedata/synth/sub-01_task-faces.vhdr"


def test_inspect_without_sourcedata(tmp_path: Path) -> None:
    at = _app(tmp_path)
    assert not at.exception
    assert any("No recordings found" in i.value for i in at.info)


def test_configure_shows_validation_errors(project: tuple[Path, Path]) -> None:
    root, _ = project
    at = _app(root, page="2 · Configure", file="sourcedata/synth/sub-01_task-faces.vhdr")
    assert not at.exception
    at.text_input(key="cfg_task").set_value("not valid!").run()
    assert any("dataset.task" in e.value for e in at.error)


def test_ui_config_converts_like_hand_written(project: tuple[Path, Path]) -> None:
    """Acceptance A1: a config made in the UI gives the same BIDS output as a hand-written one."""
    root, hand_cfg = project
    # Hand-written reference conversion.
    assert convert(hand_cfg, project_root=root)[0].error is None
    reference = root.parent / "reference_rawdata"
    shutil.copytree(root / "rawdata", reference)
    shutil.rmtree(root / "rawdata")

    # The same choices made in the app. Editing the mapping table is simulated through the
    # session state that backs the data editor (AppTest cannot type into a data editor).
    at = _app(
        root,
        page="2 · Configure",
        file="sourcedata/synth/sub-01_task-faces.vhdr",
        mapping_rows=[
            {"code": "1", "n": 50, "trial_type": "stimulus/face"},
            {"code": "2", "n": 50, "trial_type": "stimulus/car"},
        ],
    )
    at.text_input(key="cfg_name").set_value("Synthetic UI")
    at.text_input(key="cfg_task").set_value("faces")
    at.text_input(key="cfg_reference").set_value("Cz")
    at.selectbox(key="cfg_unmapped").set_value("error")
    at.run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    at.button(key="cfg_save").click().run()
    saved = root / "code" / "configs" / "synthetic_ui.yaml"
    assert saved.exists()
    ui_cfg = yaml.safe_load(saved.read_text())
    assert ui_cfg["events"]["mapping"] == {"1": "stimulus/face", "2": "stimulus/car"}

    # Convert through the app's third page.
    at.session_state["page"] = "3 · Convert + QA"
    at.run()
    at.selectbox(key="cv_config").set_value("code/configs/synthetic_ui.yaml")
    at.button(key="cv_run").click().run()
    assert not at.exception
    [row] = at.session_state["results"]
    assert row["error"] is None and row["qa"] in ("PASS", "WARN")

    eeg = Path("sub-01") / "eeg"
    for name in ("sub-01_task-faces_events.tsv", "sub-01_task-faces_channels.tsv"):
        assert filecmp.cmp(root / "rawdata" / eeg / name, reference / eeg / name, shallow=False)


def test_save_refuses_overwrite(project: tuple[Path, Path]) -> None:
    root, _ = project
    target = root / "code" / "configs" / "synthetic.yaml"
    target.write_text("keep me")
    at = _app(root, page="2 · Configure", file="sourcedata/synth/sub-01_task-faces.vhdr")
    at.text_input(key="cfg_name").set_value("Synthetic").run()
    at.button(key="cfg_save").click().run()
    assert target.read_text() == "keep me"
    assert any("exists" in e.value for e in at.error)


def test_finds_recordings_through_symlinks(
    tmp_path: Path, synthetic_files: dict[str, Path]
) -> None:
    """Datasets symlinked into sourcedata/ from another drive must be listed."""
    external = tmp_path / "external_drive" / "study"
    external.mkdir(parents=True)
    shutil.copy(synthetic_files["edf"], external / "sub-01_task-x_eeg.edf")
    (tmp_path / "project" / "sourcedata").mkdir(parents=True)
    (tmp_path / "project" / "sourcedata" / "study").symlink_to(external)
    at = _app(tmp_path / "project")
    assert at.selectbox(key="inspect_file").options == ["sourcedata/study/sub-01_task-x_eeg.edf"]
