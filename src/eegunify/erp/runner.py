"""Plan and execute ERP stages with a per-stage cache (docs/BLUEPRINT_PHASES.md section 4.2).

For every (subject, stage) the runner computes the expected ``stage_key`` and compares it with
the one stored in that stage's provenance file:

- ``ok``: key matches, the run succeeded and every recorded output exists -> skipped.
- ``stale``: key differs or an output is gone -> rerun.
- ``missing``: never run -> run.
- ``failed``: the last run failed -> rerun.

Keys chain through upstream *keys*, not file hashes, so changing ``epochs.reject`` changes the
epochs key and everything downstream, while preproc and ica stay cached. Only the first stage
hashes files (the canonical rawdata). ``force`` reruns a stage and everything after it;
``from_``/``to`` limit which stages may run at all.

The runner refuses rather than guesses: outputs without a provenance file, an unreadable
provenance file, or one written by another eegunify major version stop the run, unless that
stage is forced. A failing subject is reported and excluded from group stages (the group
stage receives the exclusions and reports them as ``GROUP_SUBJECTS_EXCLUDED``).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from eegunify import __version__
from eegunify.config.loader import resolve_for_subject
from eegunify.config.schema import Config
from eegunify.erp import paths
from eegunify.erp.hashing import (
    file_sha256,
    major_version,
    normalize,
    software_versions,
    stage_key,
)
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    UpstreamOutput,
    registered_stages,
    write_text_atomic,
)

log = logging.getLogger(__name__)

Status = Literal["ok", "stale", "missing", "failed"]
GROUP = "group"
"""Subject label shown for group-level stages in plans and status tables."""


class RunnerRefusedError(RuntimeError):
    """Existing outputs can't be trusted; the message says what to delete or force."""


@dataclass
class PlanItem:
    stage: str
    subject: str | None
    status: Status
    key: str
    reason: str = ""
    run: bool = False
    refusal: str | None = None

    @property
    def label(self) -> str:
        return GROUP if self.subject is None else self.subject


@dataclass
class Outcome:
    item: PlanItem
    status: Literal["ok", "failed", "skipped"]
    message: str = ""
    result: StageResult | None = None


@dataclass
class _Record:
    """A parsed provenance file."""

    stage_key: str
    status: str
    outputs: list[Path]
    raw: dict[str, Any] = field(default_factory=dict)


class Runner:
    """Run the ERP stages of one config.

    Parameters
    ----------
    cfg
        The dataset config (per-subject overrides are applied here).
    project_root
        Folder that holds ``rawdata/`` and ``derivatives/``.
    stages
        Ordered stages; default: the registered built-in stages.
    """

    def __init__(
        self, cfg: Config, project_root: Path, stages: Sequence[Stage] | None = None
    ) -> None:
        self.cfg = cfg
        self.root = Path(os.path.abspath(project_root))
        all_stages = list(stages) if stages is not None else registered_stages()
        self.all_names = [s.name for s in all_stages]
        self.stages = [s for s in all_stages if s.enabled(cfg)]
        self.names = [s.name for s in self.stages]
        seen_group = False
        for s in self.stages:
            if s.level == "group":
                seen_group = True
            elif seen_group:
                raise ValueError(f"subject stage {s.name!r} comes after a group stage")

    # --- planning ------------------------------------------------------------------------------

    def _check_names(self, names: Iterable[str | None], what: str) -> None:
        for n in names:
            if n is None:
                continue
            if n not in self.all_names:
                raise ValueError(f"{what}: unknown stage {n!r}; stages: {self.all_names}")
            if n not in self.names:
                raise ValueError(f"{what}: stage {n!r} is disabled by the config")

    def _subjects(self, subjects: Sequence[str] | None) -> list[str]:
        known = self.cfg.recordings.subjects
        if not subjects:
            return list(known)
        unknown = [s for s in subjects if s not in known]
        if unknown:
            raise ValueError(f"subjects {unknown} not in the config ({known})")
        return list(subjects)

    def _cfg_for(self, subject: str | None) -> Config:
        return self.cfg if subject is None else resolve_for_subject(self.cfg, subject)

    def _read(self, stage: Stage, subject: str | None) -> tuple[_Record | None, str | None]:
        """The stored record, or a refusal message."""
        prov = paths.provenance_path(self.root, stage.name, self.cfg, subject)
        force_hint = f"or use --force {stage.name}"
        if not prov.exists():
            folder = (
                paths.group_dir(self.root, self.cfg)
                if subject is None
                else paths.subject_dir(self.root, stage.name, self.cfg, subject)
            )
            if folder.is_dir() and any(p.is_file() for p in folder.iterdir()):
                rel = Path(os.path.relpath(folder, self.root)).as_posix()
                return None, f"{rel} has outputs but no provenance: delete it {force_hint}"
            return None, None
        try:
            data = json.loads(prov.read_text(encoding="utf-8"))
            rec = _Record(
                stage_key=str(data["stage_key"]),
                status=str(data["status"]),
                outputs=[self.root / p for p in data.get("outputs", [])],
                raw=data,
            )
        except (ValueError, KeyError, TypeError) as err:
            rel = Path(os.path.relpath(prov, self.root)).as_posix()
            return None, f"{rel} is unreadable ({err}): delete it {force_hint}"
        theirs = major_version(str(data.get("eegunify_version", "?")))
        if theirs != major_version(__version__):
            rel = Path(os.path.relpath(prov.parent, self.root)).as_posix()
            return None, (
                f"{rel} was written by eegunify {data.get('eegunify_version')} (this is "
                f"{__version__}): delete it {force_hint}"
            )
        return rec, None

    def _key(self, stage: Stage, cfg: Config, upstream: dict[str, str]) -> str:
        return stage_key(
            stage=stage.name,
            schema_version=stage.schema_version,
            config=stage.hash_config(cfg, self.root),
            upstream=upstream,
            versions=software_versions(),
        )

    def _assess(self, stage: Stage, subject: str | None, key: str, extra: str = "") -> PlanItem:
        rec, refusal = self._read(stage, subject)
        item = PlanItem(stage.name, subject, "missing", key, refusal=refusal)
        if refusal:
            item.reason = refusal
        elif rec is None:
            item.reason = extra or "not run yet"
        elif rec.status != "ok":
            item.status, item.reason = "failed", str(rec.raw.get("message", "last run failed"))
        elif rec.stage_key != key:
            item.status, item.reason = "stale", "inputs or settings changed"
        elif missing := [p for p in rec.outputs if not p.exists()]:
            rel = Path(os.path.relpath(missing[0], self.root)).as_posix()
            item.status, item.reason = "stale", f"output missing: {rel}"
        else:
            item.status = "ok"
        if extra and item.status != "ok" and not refusal:
            item.reason = extra
        return item

    def _source_upstream(self, stage: Stage, cfg: Config, subject: str) -> dict[str, str]:
        ctx = StageContext(self.root, cfg, stage.name, subject, "")
        return {p.name: file_sha256(p) for p in stage.source_files(ctx)}

    def plan(
        self,
        subjects: Sequence[str] | None = None,
        from_: str | None = None,
        to: str | None = None,
        force: Iterable[str] = (),
    ) -> list[PlanItem]:
        """What would run, in execution order (subject stages per subject, then group)."""
        force = list(force)
        self._check_names([from_, to], "--from/--to")
        self._check_names(force, "--force")
        if not self.stages:
            return []
        lo = self.names.index(from_) if from_ else 0
        hi = self.names.index(to) if to else len(self.names) - 1
        if lo > hi:
            raise ValueError(f"--from {from_} comes after --to {to}")
        forced_from = min((self.names.index(f) for f in force), default=len(self.names))
        subs = self._subjects(subjects)
        items: list[PlanItem] = []
        last_keys: dict[str, str] = {}
        for i, stage in enumerate(self.stages):
            if stage.level == "subject":
                for sub in subs:
                    cfg = self._cfg_for(sub)
                    if i == 0:
                        upstream = self._source_upstream(stage, cfg, sub)
                        extra = "" if upstream else "no rawdata: run `eegunify convert` first"
                    else:
                        upstream = {self.names[i - 1]: last_keys[sub]}
                        extra = ""
                    item = self._assess(stage, sub, self._key(stage, cfg, upstream), extra)
                    last_keys[sub] = item.key
                    items.append(item)
            else:
                upstream = {f"sub-{s}:{self.names[i - 1]}": last_keys[s] for s in subs} if i else {}
                item = self._assess(stage, None, self._key(stage, self.cfg, upstream))
                items.append(item)
            for it in items:
                if it.stage != stage.name:
                    continue
                in_range = lo <= i <= hi
                it.run = in_range and (it.status != "ok" or i >= forced_from)
                if it.run and i >= forced_from:
                    it.refusal = None
                    if it.status == "ok":
                        it.reason = "forced"
        return items

    def status(self, subjects: Sequence[str] | None = None) -> list[PlanItem]:
        """Subject x stage status, nothing run (``eegunify erp status``)."""
        return self.plan(subjects)

    # --- execution -----------------------------------------------------------------------------

    def run(
        self,
        subjects: Sequence[str] | None = None,
        from_: str | None = None,
        to: str | None = None,
        force: Iterable[str] = (),
    ) -> list[Outcome]:
        """Execute the plan. Raises :class:`RunnerRefusedError` before running anything if a stage
        that would run has untrustworthy outputs."""
        items = self.plan(subjects, from_, to, force)
        run_subjects = self._subjects(subjects)
        refusals = [f"{it.stage} {it.label}: {it.refusal}" for it in items if it.run and it.refusal]
        if refusals:
            raise RunnerRefusedError("\n".join(refusals))
        by_name = {s.name: s for s in self.stages}
        # Per subject: the last stage's state, carried along the chain.
        state: dict[str, UpstreamOutput | None] = {}
        broken: dict[str, str] = {}
        outcomes: list[Outcome] = []
        for it in items:
            stage = by_name[it.stage]
            if stage.level == "group":
                outcomes.append(self._run_group(stage, it, run_subjects, state, broken))
                continue
            sub = it.subject
            assert sub is not None
            if sub in broken:
                msg = broken[sub]
                outcomes.append(Outcome(it, "failed" if it.run else "skipped", msg))
                continue
            if not it.run:
                if it.status != "ok":
                    broken[sub] = f"upstream {it.stage} is {it.status}; run it first"
                else:
                    state[sub] = self._upstream_from_record(stage, sub)
                outcomes.append(Outcome(it, "skipped", it.reason or it.status))
                continue
            cfg = self._cfg_for(sub)
            ctx = StageContext(self.root, cfg, stage.name, sub, it.key)
            sources = stage.source_files(ctx) if self.names.index(stage.name) == 0 else []
            inputs = StageInputs(source_files=sources, upstream=state.get(sub))
            if self.names.index(stage.name) == 0 and not sources:
                result = StageResult("failed", message="no rawdata: run `eegunify convert` first")
            else:
                result = self._execute(stage, ctx, inputs)
            self._write_provenance(stage, ctx, inputs, result)
            if result.status == "ok":
                state[sub] = UpstreamOutput(stage.name, sub, it.key, result.outputs)
                outcomes.append(Outcome(it, "ok", result.message, result))
            else:
                broken[sub] = f"{stage.name} failed: {result.message}"
                outcomes.append(Outcome(it, "failed", result.message, result))
        return outcomes

    def _run_group(
        self,
        stage: Stage,
        it: PlanItem,
        subjects: list[str],
        state: dict[str, UpstreamOutput | None],
        broken: dict[str, str],
    ) -> Outcome:
        if not it.run:
            return Outcome(it, "skipped", it.reason or it.status)
        included = {s: u for s in subjects if s not in broken and (u := state.get(s)) is not None}
        excluded = {s: broken.get(s, "no upstream output") for s in subjects if s not in included}
        prev = self.names[self.names.index(stage.name) - 1]
        # The key covers the subjects actually included, so a later run with all of them
        # passing differs from this one and reruns.
        upstream = {f"sub-{s}:{prev}": u.stage_key for s, u in sorted(included.items())}
        key = self._key(stage, self.cfg, upstream)
        if not included:
            return Outcome(it, "failed", "no subject passed the upstream stages")
        ctx = StageContext(self.root, self.cfg, stage.name, None, key)
        inputs = StageInputs(subjects=included, excluded=excluded)
        result = self._execute(stage, ctx, inputs)
        self._write_provenance(stage, ctx, inputs, result)
        status: Literal["ok", "failed"] = "ok" if result.status == "ok" else "failed"
        return Outcome(it, status, result.message, result)

    def _upstream_from_record(self, stage: Stage, subject: str) -> UpstreamOutput:
        rec, _ = self._read(stage, subject)
        assert rec is not None  # status was ok
        return UpstreamOutput(stage.name, subject, rec.stage_key, rec.outputs)

    @staticmethod
    def _execute(stage: Stage, ctx: StageContext, inputs: StageInputs) -> StageResult:
        log.info("%s %s: running", stage.name, ctx.subject or GROUP)
        try:
            return stage.run(ctx, inputs)
        except Exception as err:  # a failing subject must not stop the others
            log.exception("%s %s failed", stage.name, ctx.subject or GROUP)
            return StageResult("failed", message=f"{type(err).__name__}: {err}")

    def _rel(self, p: Path) -> str:
        return Path(os.path.relpath(os.path.abspath(p), self.root)).as_posix()

    def _write_provenance(
        self, stage: Stage, ctx: StageContext, inputs: StageInputs, result: StageResult
    ) -> None:
        if inputs.upstream is not None:
            upstream = {inputs.upstream.stage: inputs.upstream.stage_key}
        elif inputs.subjects:
            upstream = {f"sub-{s}": u.stage_key for s, u in sorted(inputs.subjects.items())}
        else:
            upstream = {self._rel(p): file_sha256(p) for p in inputs.source_files}
        record = {
            "stage": stage.name,
            "stage_key": ctx.stage_key,
            "schema_version": stage.schema_version,
            "status": result.status,
            "message": result.message,
            "subject": ctx.subject,
            "upstream_keys": upstream,
            "excluded_subjects": dict(inputs.excluded),
            "outputs": [self._rel(p) for p in result.outputs],
            "config": normalize(stage.hash_config(ctx.config, self.root)),
            "qa": [{"id": c.id, "level": c.level, "message": c.message} for c in result.qa],
            "extra": result.extra,
            "eegunify_version": __version__,
            "versions": software_versions(),
            "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        prov = paths.provenance_path(self.root, stage.name, self.cfg, ctx.subject)
        write_text_atomic(prov, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
