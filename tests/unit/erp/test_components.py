"""Component library: presets validate, resolution rules, overrides, extra files."""

from pathlib import Path

import pytest

from eegunify.config.erp_sections import ComponentsSection
from eegunify.erp.components.library import (
    LibraryError,
    load_library,
    presets,
    resolve,
    split_key,
)


def test_builtin_presets_validate() -> None:
    lib = load_library()
    assert {"erpcore", "classic", "iscev"} <= set(presets(lib))
    for comp in ("n170", "p3", "n2pc", "mmn", "n400", "ern", "lrp"):
        assert f"{comp}@erpcore" in lib
        assert "Kappenman et al. 2021" in lib[f"{comp}@erpcore"].citation
    for key, c in lib.items():
        assert c.window_ms[0] < c.window_ms[1], key
        if c.lateralized_pairs:
            assert c.contrast == "contra_minus_ipsi", key


def test_erpcore_n170_matches_table() -> None:
    """Kappenman et al. 2021 Tables 1-2 (checked against PMC7909723); finding F4."""
    n170 = load_library()["n170@erpcore"]
    assert n170.window_ms == (110, 150)
    assert n170.rois.primary == ["PO8"]
    assert n170.contrast == "face_minus_car"
    assert n170.reference == "average"
    assert n170.polarity == "negative"


def test_resolve_prefers_preset_then_falls_back_with_warning() -> None:
    res = resolve(ComponentsSection(preset="erpcore", use=["n170", "p1", "n2pc@classic"]))
    assert res.components["n170"].key == "n170@erpcore"
    assert res.components["p1"].key == "p1@classic"
    assert res.components["n2pc"].key == "n2pc@classic"
    assert len(res.warnings) == 1 and "p1" in res.warnings[0]


def test_resolve_overrides_deep_merge_and_hash_payload() -> None:
    res = resolve(
        ComponentsSection(use=["n170"], overrides={"n170": {"rois": {"secondary": ["P8"]}}})
    )
    c = res.components["n170"]
    assert c.overridden
    assert c.definition.rois.primary == ["PO8"]
    assert c.definition.rois.secondary == ["P8"]
    payload = res.to_json()
    assert payload["n170"]["key"] == "n170@erpcore"
    assert payload["n170"]["window_ms"] == [110.0, 150.0]


@pytest.mark.parametrize(
    ("section", "msg"),
    [
        (ComponentsSection(use=["n999"]), "unknown component 'n999'"),
        (ComponentsSection(use=["n170@nope"]), "unknown component 'n170@nope'"),
        (ComponentsSection(use=["n170", "n170@classic"]), "listed twice"),
        (ComponentsSection(use=["p3"], overrides={"n170": {}}), "not in components.use"),
        (
            ComponentsSection(use=["n170"], overrides={"n170": {"window_ms": [150, 110]}}),
            "start < end",
        ),
        (ComponentsSection(use=["n170"], overrides={"n170": {"windw": 1}}), "windw"),
    ],
)
def test_resolve_errors(section: ComponentsSection, msg: str) -> None:
    with pytest.raises(LibraryError, match=msg):
        resolve(section)


def test_library_paths_add_and_refuse_duplicates(tmp_path: Path) -> None:
    extra = tmp_path / "code" / "configs" / "components" / "mine.yaml"
    extra.parent.mkdir(parents=True)
    extra.write_text(
        "p2@mine:\n  name: P2\n  citation: lab notes\n  window_ms: [150, 250]\n"
        "  rois: {primary: [Cz]}\n  polarity: positive\n",
        encoding="utf-8",
    )
    sec = ComponentsSection(use=["p2"], library_paths=["code/configs/components/mine.yaml"])
    assert resolve(sec, project_root=tmp_path).components["p2"].key == "p2@mine"
    extra.write_text(
        "n170@erpcore:\n  name: x\n  citation: x\n  window_ms: [1, 2]\n"
        "  rois: {primary: [Cz]}\n  polarity: positive\n",
        encoding="utf-8",
    )
    with pytest.raises(LibraryError, match="defined in"):
        resolve(sec, project_root=tmp_path)
    with pytest.raises(LibraryError, match="not found"):
        load_library([tmp_path / "missing.yaml"])


@pytest.mark.parametrize("key", ["N170@erpcore", "n170", "n170@", "n170@erp-core"])
def test_bad_keys(key: str) -> None:
    with pytest.raises(LibraryError):
        split_key(key)
