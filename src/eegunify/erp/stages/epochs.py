"""epochs stage (Phase 4): ``desc-clean`` (or ``desc-preproc``) -> one ``_epo.fif`` per set.

Outputs in ``derivatives/eegunify-epochs/sub-*/[ses-*/]eeg/``, for each ``epochs.sets`` entry:

- ``*_desc-<set>_epo.fif``: the kept, baseline-corrected epochs. Not written when QA fails.
- ``*_desc-<set>_log.tsv``: one row per selected event: ``idx``, ``onset_s``, ``label``,
  ``trial_type``, ``code`` (the rawdata ``value``), ``kept``, ``reason``, ``ptp_uv``, ``step_uv``.

plus ``*_desc-epochs_qa.json`` (QA IDs in ``erp/qa.py``). The input is the ica stage's
``desc-clean`` file, or ``desc-preproc`` when ``artifacts.method: none`` (no copy is made).
The stage is disabled when the config defines no epoch set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import mne

from eegunify.config.schema import Config
from eegunify.erp import paths, qa
from eegunify.erp.components.library import resolve
from eegunify.erp.epochs import (
    NO_RESPONSE,
    baseline_offset_uv,
    condition_counts,
    make_epochs,
    rawdata_codes,
)
from eegunify.erp.events import matches
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    register_stage,
)

INPUT_SUFFIXES = ("_desc-clean_eeg.fif", "_desc-preproc_eeg.fif")


def input_file(inputs: StageInputs) -> Path:
    assert inputs.upstream is not None
    for suffix in INPUT_SUFFIXES:
        for p in inputs.upstream.outputs:
            if p.name.endswith(suffix):
                return p
    raise FileNotFoundError(f"{inputs.upstream.stage} wrote no {' or '.join(INPUT_SUFFIXES)}")


def checked_conditions(cfg: Config, project_root: Path) -> dict[str, list[str]]:
    """``<set>/<condition>`` -> selectors whose trial counts ``EPOCHS_MIN_TRIALS`` checks: the
    conditions of the used components' contrasts on their epoch sets, or, without components,
    every condition on every set (sets where a condition matches nothing are skipped later)."""
    out: dict[str, list[str]] = {}
    if cfg.components.use:
        for rc in resolve(cfg.components, project_root=project_root).components.values():
            d = rc.definition
            if d.contrast and d.contrast in cfg.contrasts:
                c = cfg.contrasts[d.contrast]
                for cond in c.conditions:
                    out[f"{d.epoch_set}/{cond}"] = cfg.conditions[cond]
        return out
    for set_name in cfg.epochs.sets:
        for cond, sels in cfg.conditions.items():
            out[f"{set_name}/{cond}"] = sels
    return out


@register_stage
class EpochsStage(Stage):
    name = "epochs"
    schema_version = 2  # 2: NO_RESPONSE rows, rejected fraction among candidates

    def enabled(self, cfg: Config) -> bool:
        return bool(cfg.epochs.sets)

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        return {
            "epochs": cfg.epochs,
            # Only QA (EPOCHS_MIN_TRIALS) depends on these, but the QA file is an output.
            "checked_conditions": checked_conditions(cfg, project_root),
        }

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        assert ctx.subject is not None
        cfg = ctx.config
        src = input_file(inputs)
        raw = mne.io.read_raw_fif(src, preload=True, verbose=False)
        events_tsv = next(
            (
                p
                for p in paths.rawdata_files(ctx.project_root, cfg, ctx.subject)
                if p.name.endswith("_events.tsv")
            ),
            None,
        )
        codes = rawdata_codes(events_tsv)
        wanted = checked_conditions(cfg, ctx.project_root)
        results = {
            name: make_epochs(raw, name, eset, cfg.epochs.reject, codes)
            for name, eset in cfg.epochs.sets.items()
        }
        # Events without the required response were never candidates (ERPLAB: not in a bin),
        # so they count neither as rejected nor in the total.
        cand = {n: r.log[r.log["reason"] != NO_RESPONSE] for n, r in results.items()}
        n_total = sum(len(c) for c in cand.values())
        n_rej = sum(int((~c["kept"]).sum()) for c in cand.values())
        by_set = {n: f"{int((~c['kept']).sum())}/{len(c)}" for n, c in cand.items()}
        counts: dict[str, int] = {}
        offsets: dict[str, float] = {}
        for key, sels in wanted.items():
            set_name, _, cond = key.partition("/")
            res = results.get(set_name)
            if res is None:
                continue
            if not any(matches(lab, s) for lab in res.log["label"] for s in sels):
                continue  # condition not in this set (response conditions on a stim set)
            counts[key] = condition_counts(res.log, {cond: sels})[cond]
            eset = cfg.epochs.sets[set_name]
            window = eset.baseline_ms or (eset.tmin_ms, 0.0)
            ids = [k for k in res.epochs.event_id if any(matches(k, s) for s in sels)]
            sub = res.epochs[ids] if ids and counts[key] else None
            offsets[key] = baseline_offset_uv(sub, window) if sub is not None else float("nan")
        checks = [
            qa.rejected_fraction_check(n_rej, n_total, by_set),
            qa.min_trials_check(counts, cfg.epochs.reject.min_trials_per_condition),
            qa.baseline_drift_check(offsets),
        ]
        failed = qa.worst(checks) == "fail"
        outputs: list[Path] = []
        for name, res in results.items():
            log_path = ctx.output(description=name, suffix="log", extension=".tsv")
            with atomic_path(log_path) as tmp:
                res.log.to_csv(tmp, sep="\t", index=False, na_rep="n/a", encoding="utf-8")
            outputs.append(log_path)
            if not failed:
                epo = ctx.output(description=name, suffix="epo", extension=".fif")
                with atomic_path(epo) as tmp:
                    res.epochs.save(tmp, fmt="single", overwrite=True, verbose=False)
                outputs.append(epo)
        sets = {n: r.details for n, r in results.items()}
        qa_path = ctx.output(description="epochs", suffix="qa", extension=".json")
        extra = {
            "subject": ctx.subject,
            "input": src.name,
            "sets": sets,
            "kept_per_condition": counts,
            # Onsets come from rawdata, where Phase 1 already applied these shifts (F2): recorded
            # for traceability, never applied again.
            "onset_shift_ms_in_rawdata": dict(cfg.events.onset_shift_ms),
        }
        qa.write_qa_json(qa_path, self.name, checks, extra)
        outputs.append(qa_path)
        status: Literal["ok", "failed"] = "failed" if failed else "ok"
        failing = [c.message for c in checks if c.level == "fail"]
        return StageResult(
            status,
            outputs,
            checks,
            message="; ".join(failing),
            extra={"kept_per_condition": counts, "n_events": n_total, "n_rejected": n_rej},
        )
