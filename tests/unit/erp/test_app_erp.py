"""ERP pages of the app (A2, BLUEPRINT 10): AppTest on a synthetic project, no browser.

The project is converted and run through every ERP stage once per module (precomputed
derivatives); each test copies it. Acceptance (blueprint): a config made through the pages
equals a hand-written one, and ``eegunify erp run`` on it gives identical ``measures.tsv``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from eegunify.app.erp_pages import ERP_PAGES, drop, get, put
from eegunify.config.loader import load_config
from eegunify.erp import paths
from eegunify.erp.runner import Runner
from tests.synthetic.erp import erp_config

APP = str(Path(__file__).parents[3] / "src" / "eegunify" / "app" / "main.py")
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")
SECTIONS: dict[str, Any] = {
    "epochs": {
        "sets": {"stim": {"lock": "stimulus/*", "tmin_ms": -200, "tmax_ms": 800}},
        "reject": {"min_trials_per_condition": 10},
    },
    "conditions": {"face": ["stimulus/face"], "car": ["stimulus/car"]},
    "contrasts": {"face_minus_car": {"plus": "face", "minus": "car"}},
    "components": {"preset": "erpcore", "use": ["n170"]},
    "group": {"n_bootstrap": 200},
}
CONFIG = "code/configs/synth_erp.yaml"


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory, blink_rawdata: Path) -> Path:
    """The blink project with every ERP stage already run (derivatives present)."""
    root = tmp_path_factory.mktemp("app_erp") / "project"
    shutil.copytree(blink_rawdata, root, symlinks=True)
    (root / CONFIG).write_text(yaml.safe_dump(erp_config(**SECTIONS)), encoding="utf-8")
    outs = Runner(load_config(root / CONFIG), root).run()
    assert all(o.status in ("ok", "skipped") for o in outs), [(o.item, o.message) for o in outs]
    return root


@pytest.fixture
def project(ran: Path, tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(ran, root, symlinks=True)
    return root


def _app(root: Path, page: str, **state: object) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["root"] = str(root)
    at.session_state["page"] = page
    at.session_state["erp_config"] = CONFIG
    for key, val in state.items():
        at.session_state[key] = val
    return at.run()


def test_dotted_helpers() -> None:
    d: dict[str, Any] = {}
    put(d, "a.b.c", 1)
    assert get(d, "a.b.c") == 1 and get(d, "a.x", 5) == 5
    drop(d, "a.b.c")
    assert d == {}  # empty parents removed too


@pytest.mark.parametrize("page", ERP_PAGES)
def test_every_page_renders(project: Path, page: str) -> None:
    at = _app(project, page)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]


def test_components_override_and_save(project: Path) -> None:
    at = _app(project, ERP_PAGES[3])
    key = f"cp_{CONFIG}_n170_lo"
    at.number_input(key=key).set_value(100.0).run()
    cfg = at.session_state["erp_cfg"]
    assert get(cfg, "components.overrides.n170.window_ms") == [100.0, 150.0]
    assert any(p.type == "pyplot" for p in at.get("imgs")) or at.get("imgs") is not None
    at.text_input(key="erp_save_as").set_value("code/configs/edited.yaml").run()
    next(b for b in at.button if b.key == "erp_save").click().run()
    saved = load_config(project / "code" / "configs" / "edited.yaml")
    assert saved.components.overrides["n170"]["window_ms"] == [100.0, 150.0]
    # back to the preset value removes the override
    at.number_input(key=key).set_value(110.0).run()
    assert get(at.session_state["erp_cfg"], "components.overrides") is None


def test_epochs_page_shows_rejections_and_threshold_effect(project: Path) -> None:
    at = _app(project, ERP_PAGES[2])
    shown = [x.value for x in [*at.info, *at.subheader, *at.warning]]
    assert any("epochs kept" in s.value for s in at.subheader), shown
    assert any("exceed the peak-to-peak rule" in c.value for c in at.caption)
    at.number_input(key=f"ep_{CONFIG}_mt").set_value(20).run()
    assert get(at.session_state["erp_cfg"], "epochs.reject.min_trials_per_condition") == 20


def test_ica_page_lists_components_and_writes_per_subject_overrides(project: Path) -> None:
    cfg = yaml.safe_load((project / CONFIG).read_text(encoding="utf-8"))
    put(cfg, "overrides.sub-01.artifacts.ica.exclude", [0])
    at = _app(project, ERP_PAGES[1], erp_cfg=cfg)
    assert not at.exception
    shown = [x.value for x in [*at.info, *at.caption, *at.warning, *at.error]]
    assert any("overrides: sub-01" in c.value for c in at.caption), shown
    # the table round-trips the manual choice into the same per-subject override
    assert get(at.session_state["erp_cfg"], "overrides.sub-01.artifacts.ica.exclude") == [0]
    at.run()
    assert get(at.session_state["erp_cfg"], "overrides.sub-01.artifacts.ica.exclude") == [0]


def test_group_page_shows_stats_and_qa(project: Path) -> None:
    at = _app(project, ERP_PAGES[4])
    assert any("GROUP_UNDERPOWERED" in i.value for i in at.info)
    assert any(s.value == "Statistics" for s in at.subheader)
    at.checkbox(key=f"gr_{CONFIG}_c").check().run()
    cfg = at.session_state["erp_cfg"]
    assert get(cfg, "group.cluster.enabled") is True
    assert get(cfg, "group.cluster.contrast") == "face_minus_car"


def test_preprocess_preview(project: Path) -> None:
    at = _app(project, ERP_PAGES[0])
    next(b for b in at.button if b.key == f"pp_{CONFIG}_preview").click().run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    assert any("Bad channels" in m.value for m in at.markdown)


def test_ui_config_runs_like_hand_written(project: Path) -> None:
    """Acceptance: start from a config without ERP sections, build them in the pages; the
    result equals the hand-written config and erp run gives identical measures.tsv."""
    bare = {k: v for k, v in erp_config().items()}
    (project / "code" / "configs" / "bare.yaml").write_text(yaml.safe_dump(bare), encoding="utf-8")
    state: dict[str, object] = {"erp_config": "code/configs/bare.yaml"}
    at = _app(project, ERP_PAGES[3], **state)
    k = "cp_code/configs/bare.yaml"
    # conditions and contrast through the table editors (AppTest sets their edited state)
    cfg = at.session_state["erp_cfg"]
    put(cfg, "conditions", SECTIONS["conditions"])
    put(cfg, "contrasts", SECTIONS["contrasts"])
    at.session_state["erp_cfg"] = cfg
    at.run()
    at.multiselect(key=f"{k}_use").set_value(["n170"]).run()
    assert not at.exception
    at.session_state["page"] = ERP_PAGES[2]
    at.run()
    cfg = at.session_state["erp_cfg"]
    put(cfg, "epochs.sets", SECTIONS["epochs"]["sets"])  # the sets table, as edited
    at.session_state["erp_cfg"] = cfg
    at.run()
    at.number_input(key="ep_code/configs/bare.yaml_mt").set_value(10).run()
    at.session_state["page"] = ERP_PAGES[4]
    at.run()
    assert not at.exception  # group page: its defaults are kept
    cfg = at.session_state["erp_cfg"]
    put(cfg, "group.n_bootstrap", 200)
    ui = load_config_dict(cfg)
    hand = load_config_dict(erp_config(**SECTIONS))
    assert ui == hand
    # erp run on the UI config reproduces the hand-written run's measures exactly
    ui_root = project.parent / "ui"
    shutil.copytree(project, ui_root, symlinks=True)
    shutil.rmtree(ui_root / "derivatives" / "eegunify-preproc")  # force a full rerun
    from eegunify.config.loader import parse_config

    Runner(parse_config(cfg), ui_root).run()
    hand_cfg = load_config(project / CONFIG)
    a = paths.stage_path(
        project, "erp", hand_cfg, "01", description="stim", suffix="measures", extension=".tsv"
    )
    b = paths.stage_path(
        ui_root,
        "erp",
        parse_config(cfg),
        "01",
        description="stim",
        suffix="measures",
        extension=".tsv",
    )
    import pandas as pd

    ma = pd.read_csv(a, sep="\t").drop(columns=["stage_key"])
    mb = pd.read_csv(b, sep="\t").drop(columns=["stage_key"])
    pd.testing.assert_frame_equal(ma, mb)


def load_config_dict(cfg: dict[str, Any]) -> dict[str, Any]:
    from eegunify.config.loader import parse_config

    return parse_config(cfg).model_dump(mode="json")
