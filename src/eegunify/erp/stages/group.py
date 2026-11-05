"""group stage (Phase 6): every included subject's erp outputs -> group results.

Outputs in ``derivatives/eegunify-group/task-<task>/`` (BLUEPRINT_PHASES section 3):

- ``task-<task>_desc-<set>_ave.fif``: grand average per wave (comment = wave name; MNE wants
  evoked files to end in ``_ave.fif``).
- ``task-<task>_measures.tsv``: every included subject's ``measures.tsv``, concatenated.
- ``task-<task>_stats.tsv``: tests and effect sizes (``eegunify.erp.group.stats_table``).
- ``task-<task>_desc-<contrast>_clusters.tsv``: cluster permutation test, if enabled.
- ``task-<task>_erpcore-comparison.tsv``: ours vs ERP CORE's published per-subject values
  (``group.reference_values: erpcore``; component = ``dataset.task``; section 8.2 rules).
- ``task-<task>_qa.json``: ``GROUP_SUBJECTS_EXCLUDED``, ``GROUP_UNDERPOWERED``,
  ``REFERENCE_VALUES_MISMATCH``.

Subjects an upstream stage failed for are left out (``inputs.excluded``) and listed by QA; in
the comparison they keep their rows, with the reason in ``note``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import mne
import pandas as pd

from eegunify.config.schema import Config
from eegunify.datasets import sha256
from eegunify.erp import paths, qa
from eegunify.erp.comparison import PAIRINGS, comparison_table, failures
from eegunify.erp.group import cluster_test, grand_averages, min_permutation_p, stats_table
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    register_stage,
)
from eegunify.reference_values import REFERENCE_DIR, read_tidy


def _files(outputs: list[Path], suffix: str) -> dict[str, Path]:
    """``{epoch set: path}`` of one subject's erp outputs ending in ``_<suffix>``."""
    out = {}
    for p in outputs:
        name = p.name
        if name.endswith(f"_{suffix}"):
            desc = next((part[5:] for part in name.split("_") if part.startswith("desc-")), "")
            out[desc] = p
    return out


@register_stage
class GroupStage(Stage):
    name = "group"
    level = "group"
    schema_version = 1

    def enabled(self, cfg: Config) -> bool:
        return bool(cfg.epochs.sets) and bool(cfg.components.use)

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        out: dict[str, Any] = {
            "group": cfg.group,
            "contrasts": cfg.contrasts,
            "subjects": list(cfg.recordings.subjects),
        }
        if cfg.group.reference_values == "erpcore":
            comp = cfg.dataset.task
            out["reference_files"] = {
                f.name: sha256(f) for f in sorted(REFERENCE_DIR.glob(f"{comp}*.tsv"))
            }
        return out

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        cfg = ctx.config
        out_dir = paths.group_dir(ctx.project_root, cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        task = cfg.dataset.task
        outputs: list[Path] = []

        def write_tsv(df: pd.DataFrame, name: str) -> Path:
            path = out_dir / name
            with atomic_path(path) as tmp:
                df.to_csv(tmp, sep="\t", index=False, na_rep="n/a", encoding="utf-8")
            outputs.append(path)
            return path

        measures = pd.concat(
            [
                pd.read_csv(p, sep="\t", dtype={"subject": str})
                for up in inputs.subjects.values()
                for p in _files(up.outputs, "measures.tsv").values()
            ],
            ignore_index=True,
        )
        write_tsv(measures, f"task-{task}_measures.tsv")
        contrasts = list(cfg.contrasts)
        stats = stats_table(measures, cfg, contrasts)
        write_tsv(stats, f"task-{task}_stats.tsv")

        # Grand averages per epoch set; the contrast evokeds also feed the cluster test.
        per_set: dict[str, dict[str, dict[str, mne.Evoked]]] = {}
        for sub, up in sorted(inputs.subjects.items()):
            for set_name, path in _files(up.outputs, "ave.fif").items():
                evs = mne.read_evokeds(path, verbose=False)
                per_set.setdefault(set_name, {})[sub] = {ev.comment: ev for ev in evs}
        extra: dict[str, Any] = {"n_subjects": len(inputs.subjects)}
        for set_name, by_sub in sorted(per_set.items()):
            ga = grand_averages(by_sub)
            path = out_dir / f"task-{task}_desc-{set_name}_ave.fif"  # MNE: *_ave.fif
            with atomic_path(path) as tmp:
                mne.write_evokeds(tmp, list(ga.values()), overwrite=True, verbose=False)
            outputs.append(path)

        checks = [qa.subjects_excluded_check(dict(inputs.excluded))]
        spec = cfg.group.cluster
        n = len(inputs.subjects)
        min_p = min_permutation_p(n, spec.tail)
        if spec.enabled and spec.contrast:
            # One wave per subject: the contrast from the first epoch set (config order) that
            # has it. Taking it from every set counted each subject once per set (0.9.2).
            cluster_set = next(
                (
                    s
                    for s in cfg.epochs.sets
                    if any(spec.contrast in w for w in per_set.get(s, {}).values())
                ),
                None,
            )
            by_sub = per_set.get(cluster_set, {}) if cluster_set else {}
            evs = [waves[spec.contrast] for waves in by_sub.values() if spec.contrast in waves]
            if len(evs) >= 2:
                table, info = cluster_test(evs, cfg)
                write_tsv(table, f"task-{task}_desc-{spec.contrast}_clusters.tsv")
                extra["cluster"] = {**info, "epoch_set": cluster_set}
        checks.append(qa.underpowered_check(n, min_p, cfg.group.alpha, spec.tail))

        if cfg.group.reference_values == "erpcore":
            if task not in PAIRINGS:
                raise ValueError(
                    f"group.reference_values erpcore: no comparison defined for task {task!r} "
                    f"(have {sorted(PAIRINGS)})"
                )
            published, summary = read_tidy(REFERENCE_DIR, task)
            subjects = list(cfg.recordings.subjects) or sorted(inputs.subjects)
            table = comparison_table(task, measures, published, summary, subjects)
            for sub, why in inputs.excluded.items():
                mask = table["subject"] == sub
                table.loc[mask, "note"] = (
                    table.loc[mask, "note"].fillna("") + f"; excluded by eegunify: {why}"
                ).str.lstrip("; ")
            write_tsv(table, f"task-{task}_erpcore-comparison.tsv")
            bad = failures(table)
            compared = table[
                ~table["status"].isin(
                    ["excluded_by_erpcore", "not_available", "not_measured", "reported"]
                )
            ]
            failing = [f"{r.subject} {r.wave} {r.measure} ({r.status})" for r in bad.itertuples()]
            checks.append(qa.reference_values_check(len(bad), len(compared), failing))
            extra["erpcore_failures"] = len(bad)

        qa_path = out_dir / f"task-{task}_qa.json"
        qa.write_qa_json(
            qa_path,
            self.name,
            checks,
            {"subjects": sorted(inputs.subjects), "excluded": dict(inputs.excluded), **extra},
        )
        outputs.append(qa_path)
        status: Literal["ok", "failed"] = "failed" if qa.worst(checks) == "fail" else "ok"
        return StageResult(status, outputs, checks, extra=extra)
