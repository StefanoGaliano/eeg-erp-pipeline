"""Stage runner with dummy stages (M-E0 acceptance): skip / run / force / stale / refuse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from eegunify.config.loader import parse_config
from eegunify.config.schema import Config
from eegunify.erp import paths
from eegunify.erp.hashing import canonical_json, stage_key
from eegunify.erp.runner import Runner, RunnerRefusedError
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    write_text_atomic,
)

SUBJECTS = ["001", "002"]


class Dummy(Stage):
    """Writes one text file holding its input; counts its calls per subject."""

    section: ClassVar[str] = ""
    fail_for: ClassVar[set[str]] = set()

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        return getattr(cfg, self.section).model_dump(mode="json") if self.section else {}

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        self.calls.append(ctx.subject)
        if ctx.subject in self.fail_for:
            raise RuntimeError("boom")
        if inputs.upstream is None:
            assert inputs.source_files, "first stage gets rawdata"
            content = ",".join(p.name for p in inputs.source_files)
        else:
            content = inputs.upstream.stage_key
            assert all(p.exists() for p in inputs.upstream.outputs)
        out = ctx.output(description=self.name, suffix="dummy", extension=".txt")
        write_text_atomic(out, content)
        return StageResult("ok", outputs=[out], extra={"content": content})


class Pre(Dummy):
    name = "preproc"
    section = "preproc"


class Ica(Dummy):
    name = "ica"
    section = "artifacts"

    def enabled(self, cfg: Config) -> bool:
        return cfg.artifacts.method == "ica"


class Epo(Dummy):
    name = "epochs"
    section = "epochs"


class Grp(Dummy):
    name = "group"
    level = "group"

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        self.calls.append(None)
        out = paths.group_dir(ctx.project_root, ctx.config) / "summary.json"
        write_text_atomic(out, json.dumps({"subjects": sorted(inputs.subjects)}))
        return StageResult("ok", outputs=[out], extra={"excluded": dict(inputs.excluded)})


def _cfg(**extra: Any) -> Config:
    return parse_config(
        {
            "dataset": {"name": "x", "source_root": "sourcedata/x", "task": "faces"},
            "recordings": {"pattern": "sub-{subject}.bdf", "subjects": SUBJECTS},
            **extra,
        }
    )


def _rawdata(root: Path, cfg: Config) -> None:
    for sub in SUBJECTS:
        d = root / "rawdata" / f"sub-{sub}" / "eeg"
        d.mkdir(parents=True, exist_ok=True)
        for ext in ("_eeg.vhdr", "_eeg.eeg", "_events.tsv"):
            (d / f"sub-{sub}_task-faces{ext}").write_text(f"{sub}{ext}", encoding="utf-8")
    assert len(paths.rawdata_files(root, cfg, "001")) == 3


def _stages() -> list[Dummy]:
    return [Pre(), Ica(), Epo(), Grp()]


def _calls(stages: list[Dummy]) -> dict[str, int]:
    return {s.name: len(s.calls) for s in stages}


def _statuses(runner: Runner) -> dict[tuple[str, str], str]:
    return {(it.label, it.stage): it.status for it in runner.status()}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _rawdata(tmp_path, _cfg())
    return tmp_path


def test_first_run_then_everything_cached(project: Path) -> None:
    stages = _stages()
    r = Runner(_cfg(), project, stages)
    assert set(_statuses(r).values()) == {"missing"}
    outcomes = r.run()
    assert [o.status for o in outcomes] == ["ok"] * 7
    assert _calls(stages) == {"preproc": 2, "ica": 2, "epochs": 2, "group": 1}
    assert set(_statuses(r).values()) == {"ok"}
    again = Runner(_cfg(), project, stages).run()
    assert {o.status for o in again} == {"skipped"}
    assert _calls(stages) == {"preproc": 2, "ica": 2, "epochs": 2, "group": 1}
    prov = json.loads(
        paths.provenance_path(project, "epochs", _cfg(), "001").read_text(encoding="utf-8")
    )
    assert prov["status"] == "ok" and prov["upstream_keys"].keys() == {"ica"}
    assert prov["outputs"] == [
        "derivatives/eegunify-epochs/sub-001/eeg/sub-001_task-faces_desc-epochs_dummy.txt"
    ]


def test_changing_one_section_reruns_only_downstream(project: Path) -> None:
    stages = _stages()
    Runner(_cfg(), project, stages).run()
    cfg2 = _cfg(epochs={"reject": {"ptp_uv": 80}})
    r = Runner(cfg2, project, stages)
    st = _statuses(r)
    assert st[("001", "preproc")] == st[("001", "ica")] == "ok"
    assert st[("001", "epochs")] == "stale" and st[("group", "group")] == "stale"
    r.run()
    assert _calls(stages) == {"preproc": 2, "ica": 2, "epochs": 4, "group": 2}


def test_per_subject_override_invalidates_one_subject(project: Path) -> None:
    stages = _stages()
    Runner(_cfg(), project, stages).run()
    cfg2 = _cfg(overrides={"sub-002": {"artifacts": {"ica": {"exclude": [1]}}}})
    Runner(cfg2, project, stages).run()
    assert stages[1].calls == ["001", "002", "002"]
    assert stages[2].calls == ["001", "002", "002"]


def test_rawdata_change_invalidates_the_chain(project: Path) -> None:
    stages = _stages()
    Runner(_cfg(), project, stages).run()
    (project / "rawdata/sub-001/eeg/sub-001_task-faces_events.tsv").write_text(
        "changed", encoding="utf-8"
    )
    st = _statuses(Runner(_cfg(), project, stages))
    assert st[("001", "preproc")] == "stale" and st[("002", "preproc")] == "ok"


def test_force_reruns_stage_and_downstream(project: Path) -> None:
    stages = _stages()
    Runner(_cfg(), project, stages).run()
    items = Runner(_cfg(), project, stages).plan(force=["ica"])
    assert [(i.stage, i.run) for i in items if i.subject == "001"] == [
        ("preproc", False),
        ("ica", True),
        ("epochs", True),
    ]
    assert all(i.reason == "forced" for i in items if i.run)
    Runner(_cfg(), project, stages).run(force=["ica"])
    assert _calls(stages) == {"preproc": 2, "ica": 4, "epochs": 4, "group": 2}


def test_from_to_and_subject_selection(project: Path) -> None:
    stages = _stages()
    r = Runner(_cfg(), project, stages)
    r.run(subjects=["001"], to="ica")
    assert _calls(stages) == {"preproc": 1, "ica": 1, "epochs": 0, "group": 0}
    # epochs for 002 can't run: its upstream never ran and --from excludes it.
    out = {(o.item.label, o.item.stage): o for o in r.run(from_="epochs", to="epochs")}
    assert out[("001", "epochs")].status == "ok"
    assert out[("002", "epochs")].status == "failed"
    assert "preproc is missing" in out[("002", "epochs")].message
    with pytest.raises(ValueError, match="comes after"):
        r.plan(from_="epochs", to="preproc")
    with pytest.raises(ValueError, match="unknown stage"):
        r.plan(force=["erp"])
    with pytest.raises(ValueError, match="not in the config"):
        r.plan(subjects=["999"])


def test_stale_when_output_deleted(project: Path) -> None:
    stages = _stages()
    Runner(_cfg(), project, stages).run()
    paths.stage_path(
        project, "ica", _cfg(), "002", description="ica", suffix="dummy", extension=".txt"
    ).unlink()
    item = next(
        i for i in Runner(_cfg(), project, stages).status() if i.label == "002" and i.stage == "ica"
    )
    assert item.status == "stale" and "output missing" in item.reason


def test_failed_subject_is_reported_and_excluded_from_group(project: Path) -> None:
    stages = _stages()
    Ica.fail_for = {"002"}
    try:
        outcomes = Runner(_cfg(), project, stages).run()
    finally:
        Ica.fail_for = set()
    by = {(o.item.label, o.item.stage): o for o in outcomes}
    assert by[("002", "ica")].status == "failed" and "boom" in by[("002", "ica")].message
    assert by[("002", "epochs")].status == "failed"
    assert by[("001", "epochs")].status == "ok"
    grp = by[("group", "group")]
    assert grp.status == "ok" and grp.result is not None
    assert list(grp.result.extra["excluded"]) == ["002"]
    st = _statuses(Runner(_cfg(), project, stages))
    assert st[("002", "ica")] == "failed"
    # Once 002 passes, the group reruns with both subjects (its key covers who was included).
    Runner(_cfg(), project, stages).run()
    summary = paths.group_dir(project, _cfg()) / "summary.json"
    assert json.loads(summary.read_text(encoding="utf-8"))["subjects"] == ["001", "002"]


def test_disabled_stage_is_bypassed(project: Path) -> None:
    stages = _stages()
    r = Runner(_cfg(artifacts={"method": "none"}), project, stages)
    assert r.names == ["preproc", "epochs", "group"]
    r.run()
    assert _calls(stages)["ica"] == 0
    prov = json.loads(
        paths.provenance_path(project, "epochs", r.cfg, "001").read_text(encoding="utf-8")
    )
    assert list(prov["upstream_keys"]) == ["preproc"]
    with pytest.raises(ValueError, match="disabled"):
        r.plan(force=["ica"])


def test_missing_rawdata_fails_cleanly(tmp_path: Path) -> None:
    item = Runner(_cfg(), tmp_path, _stages()).status()[0]
    assert item.status == "missing" and "eegunify convert" in item.reason
    out = Runner(_cfg(), tmp_path, _stages()).run(subjects=["001"])
    assert out[0].status == "failed" and "eegunify convert" in out[0].message


def test_refuses_outputs_without_provenance_unless_forced(project: Path) -> None:
    stages = _stages()
    stray = paths.stage_path(
        project, "preproc", _cfg(), "001", description="preproc", suffix="x", extension=".fif"
    )
    stray.parent.mkdir(parents=True)
    stray.write_text("?", encoding="utf-8")
    with pytest.raises(RunnerRefusedError, match=r"no provenance.*--force preproc"):
        Runner(_cfg(), project, stages).run()
    assert _calls(stages)["preproc"] == 0  # refused before running anything
    Runner(_cfg(), project, stages).run(force=["preproc"])
    assert _calls(stages)["preproc"] == 2


@pytest.mark.parametrize(
    ("content", "msg"),
    [
        ("{not json", "unreadable"),
        (json.dumps({"stage_key": "k", "status": "ok", "eegunify_version": "9.0.0"}), "9.0.0"),
    ],
)
def test_refuses_bad_provenance(project: Path, content: str, msg: str) -> None:
    prov = paths.provenance_path(project, "preproc", _cfg(), "001")
    prov.parent.mkdir(parents=True)
    prov.write_text(content, encoding="utf-8")
    with pytest.raises(RunnerRefusedError, match=msg):
        Runner(_cfg(), project, _stages()).run()


def test_group_stage_must_be_last() -> None:
    with pytest.raises(ValueError, match="after a group stage"):
        Runner(_cfg(), Path("."), [Grp(), Pre()])


def test_stage_key_is_canonical() -> None:
    a = stage_key(
        stage="s", schema_version=1, config={"b": 1.0, "a": (1, 2)}, upstream={}, versions={}
    )
    b = stage_key(
        stage="s", schema_version=1, config={"a": [1, 2], "b": 1}, upstream={}, versions={}
    )
    assert a == b
    assert a != stage_key(
        stage="s", schema_version=2, config={"a": [1, 2], "b": 1}, upstream={}, versions={}
    )
    assert canonical_json({"p": Path("a") / "b", "s": {2, 1}}) == '{"p":"a/b","s":[1,2]}'
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json(float("nan"))


def test_atomic_path_keeps_target_on_error(tmp_path: Path) -> None:
    target = tmp_path / "sub-001_eeg.fif"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError), atomic_path(target) as tmp:
        assert tmp.name.endswith("_eeg.fif") and tmp != target
        tmp.write_text("new", encoding="utf-8")
        raise RuntimeError
    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [target]
