"""Stage protocol, context, results and registry (docs/BLUEPRINT_PHASES.md section 4.1).

A stage (1) reads its inputs from paths, never from another stage's memory, (2) computes,
(3) writes its outputs atomically (:func:`atomic_path`), and (4) returns a
:class:`StageResult`. The runner, not the stage, writes the provenance file holding the
``stage_key``, so a crash mid-stage never leaves a valid-looking cache entry.

Logic belongs in plain helper functions that take and return MNE objects; ``run`` only wires
paths to helpers. That keeps the app and the tests able to call the helpers in memory.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

from eegunify.config.schema import Config
from eegunify.erp import paths
from eegunify.qa.checks import QACheck

StageLevel = Literal["subject", "group"]
STAGE_ORDER = ("preproc", "ica", "epochs", "erp", "group")
"""Canonical order of the built-in stages."""


@dataclass(frozen=True)
class StageContext:
    """Where a stage runs: project, resolved config, subject (``None`` for group stages)."""

    project_root: Path
    config: Config
    stage: str
    subject: str | None
    stage_key: str

    def output(self, *, description: str, suffix: str, extension: str) -> Path:
        """BIDS path of one of this stage's subject-level outputs."""
        if self.subject is None:
            raise ValueError("group stages write into paths.group_dir(); no BIDSPath")
        return paths.stage_path(
            self.project_root,
            self.stage,
            self.config,
            self.subject,
            description=description,
            suffix=suffix,
            extension=extension,
        )


@dataclass(frozen=True)
class UpstreamOutput:
    """What the previous stage produced for one subject (read from its provenance)."""

    stage: str
    subject: str | None
    stage_key: str
    outputs: list[Path]


@dataclass(frozen=True)
class StageInputs:
    """Inputs of one stage run.

    Subject-level stages get ``upstream`` (``None`` for the first stage, which reads
    ``source_files`` = canonical rawdata). Group stages get one entry per included subject in
    ``subjects`` and the list of subjects left out (``excluded``, with reasons).
    """

    source_files: list[Path] = field(default_factory=list)
    upstream: UpstreamOutput | None = None
    subjects: Mapping[str, UpstreamOutput] = field(default_factory=dict)
    excluded: Mapping[str, str] = field(default_factory=dict)


@dataclass
class StageResult:
    status: Literal["ok", "failed"]
    outputs: list[Path] = field(default_factory=list)
    qa: list[QACheck] = field(default_factory=list)
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    """Stage-specific facts copied into provenance (counts, chosen components, ...)."""


class Stage:
    """Base class of every stage. Subclasses set the class attributes and implement ``run``."""

    name: ClassVar[str]
    schema_version: ClassVar[int] = 1
    """Bump when the stage's outputs change meaning; invalidates every cached run."""
    level: ClassVar[StageLevel] = "subject"

    def enabled(self, cfg: Config) -> bool:
        """Disabled stages are skipped and the next stage reads the previous enabled one
        (``artifacts.method: none`` disables ica)."""
        return True

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        """Everything in the config this stage's output depends on (goes into the key)."""
        return {}

    def source_files(self, ctx: StageContext) -> list[Path]:
        """Input files of a first stage, hashed into its key. Default: canonical rawdata."""
        if ctx.subject is None:
            return []
        return paths.rawdata_files(ctx.project_root, ctx.config, ctx.subject)

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        raise NotImplementedError


_REGISTRY: dict[str, Stage] = {}
S = TypeVar("S", bound=type[Stage])


def register_stage(cls: S) -> S:
    """Class decorator: make a stage available to the default pipeline."""
    if cls.name in _REGISTRY:
        raise ValueError(f"stage {cls.name!r} registered twice")
    _REGISTRY[cls.name] = cls()
    return cls


BUILTIN_MODULES = ("eegunify.erp.stages.preproc", "eegunify.erp.stages.ica")
"""Modules defining the built-in stages, imported by :func:`registered_stages`."""


def registered_stages() -> list[Stage]:
    """Registered stages in :data:`STAGE_ORDER` (unknown names last, alphabetically)."""
    for mod in BUILTIN_MODULES:
        importlib.import_module(mod)
    rank: Callable[[str], tuple[int, str]] = lambda n: (  # noqa: E731
        STAGE_ORDER.index(n) if n in STAGE_ORDER else len(STAGE_ORDER),
        n,
    )
    return [_REGISTRY[n] for n in sorted(_REGISTRY, key=rank)]


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Yield a temporary sibling path; on success rename it onto ``path``.

    Use as ``with atomic_path(out) as tmp: raw.save(tmp)``; an exception leaves ``path``
    untouched and removes the temporary file. MNE checks file-name endings, so the temporary
    name keeps the suffix: ``x_eeg.fif`` -> ``x_eeg.tmp-<pid>_eeg.fif``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stem, _, tail = path.name.rpartition("_")
    tmp_name = f"{stem}.tmp-{os.getpid()}_{tail}" if stem else f".tmp-{os.getpid()}-{tail}"
    tmp = path.with_name(tmp_name)
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_text_atomic(path: Path, text: str) -> None:
    with atomic_path(path) as tmp:
        tmp.write_text(text, encoding="utf-8")
