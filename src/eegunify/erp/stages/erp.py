"""erp stage (Phase 5): ``desc-<set>_epo.fif`` -> averages, measurements and figures.

Outputs in ``derivatives/eegunify-erp/sub-*/[ses-*/]eeg/``, for each epoch set:

- ``*_desc-<set>_ave.fif``: one ``Evoked`` per condition and contrast (``comment`` = name).
- ``*_desc-<set>_measures.tsv``: long format, one row per measurement (section 6.6).
- ``*_desc-<set>_<component>_erp.png``: per measured component, unless ``erp.figures: false``.

plus ``*_desc-erp_qa.json`` (QA IDs in ``erp/qa.py``), which also records the trial count per
condition and the resolved component definitions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mne
import pandas as pd

from eegunify.config.schema import Config
from eegunify.erp import qa, references
from eegunify.erp.averaging import (
    MEASURES_COLUMNS,
    add_contrasts,
    average_conditions,
    measure_component,
)
from eegunify.erp.components.library import Resolution, resolve
from eegunify.erp.plots import component_figure
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    register_stage,
)


def epoch_files(inputs: StageInputs) -> dict[str, Path]:
    """Epoch set name -> its ``_epo.fif`` (from the epochs stage outputs)."""
    assert inputs.upstream is not None
    out = {}
    for p in inputs.upstream.outputs:
        if p.name.endswith("_epo.fif") and "_desc-" in p.name:
            out[p.name.split("_desc-", 1)[1].split("_", 1)[0]] = p
    return out


def _resolution(cfg: Config, project_root: Path) -> Resolution:
    if not cfg.components.use:
        return Resolution({})
    return resolve(cfg.components, project_root=project_root)


@register_stage
class ErpStage(Stage):
    name = "erp"
    schema_version = 2  # 2: component-level reference (BLUEPRINT_PHASES section 12)

    def enabled(self, cfg: Config) -> bool:
        return bool(cfg.epochs.sets)

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        return {
            "erp": cfg.erp,
            "conditions": cfg.conditions,
            "contrasts": cfg.contrasts,
            "components": _resolution(cfg, project_root).to_json(),
            "recordings_session": cfg.recordings.session,
        }

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        assert ctx.subject is not None
        cfg = ctx.config
        files = epoch_files(inputs)
        res = _resolution(cfg, ctx.project_root)
        outputs: list[Path] = []
        counts: dict[str, dict[str, int]] = {}
        sme: dict[str, float] = {}
        means: dict[str, tuple[float, str]] = {}
        no_local: dict[str, list[str]] = {}
        vep_ms: float | None = None
        rerefs: dict[str, dict[str, str]] = {}
        for set_name, path in sorted(files.items()):
            epochs = mne.read_epochs(path, preload=True, verbose=False)
            evokeds = add_contrasts(average_conditions(epochs, cfg.conditions), cfg)
            counts[set_name] = {n: ev.nave for n, ev in evokeds.items() if n in cfg.conditions}
            if evokeds:
                ave = ctx.output(description=set_name, suffix="ave", extension=".fif")
                with atomic_path(ave) as tmp:
                    mne.write_evokeds(tmp, list(evokeds.values()), overwrite=True, verbose=False)
                outputs.append(ave)
            rows: list[dict[str, Any]] = []
            for cid, rc in sorted(res.components.items()):
                if rc.definition.epoch_set != set_name:
                    continue
                # A component defined for another reference is measured on re-referenced
                # copies (references.py); the written averages keep the dataset reference.
                c_evokeds, c_epochs = evokeds, epochs
                target = references.needed(rc.definition.reference, cfg)
                if target is not None:
                    c_evokeds = {n: references.apply(ev, target, cfg) for n, ev in evokeds.items()}
                    c_epochs = references.apply(epochs, target, cfg)
                    current = references.dataset_reference(cfg)
                    rerefs[cid] = {
                        "data": current.label if current else cfg.reference.scheme,
                        "component": target.label,
                    }
                cm = measure_component(
                    rc, c_evokeds, c_epochs, cfg, subject=ctx.subject, stage_key=ctx.stage_key
                )
                rows += cm.rows
                sme[cid] = cm.sme_uv
                means[cid] = (cm.mean_uv, rc.definition.polarity)
                no_local[cid] = cm.no_local_peak
                if cid == "vep_p100":
                    vep_ms = cm.peak_latency_ms
                if cfg.erp.figures:
                    outputs.append(self._figure(ctx, set_name, cid, rc, c_evokeds))
            tsv = ctx.output(description=set_name, suffix="measures", extension=".tsv")
            with atomic_path(tsv) as tmp:
                pd.DataFrame(rows, columns=MEASURES_COLUMNS).to_csv(
                    tmp, sep="\t", index=False, na_rep="n/a", encoding="utf-8"
                )
            outputs.append(tsv)
        missing_sets = sorted(
            {rc.definition.epoch_set for rc in res.components.values()} - set(files)
        )
        checks = qa.erp_checks(sme, means, no_local, vep_ms)
        checks.append(qa.reference_check(rerefs))
        qa_path = ctx.output(description="erp", suffix="qa", extension=".json")
        extra = {
            "subject": ctx.subject,
            "n_trials": counts,
            "components": res.to_json(),
            "component_warnings": res.warnings,
            "plot_lowpass_hz": cfg.erp.plot_lowpass_hz,
            "component_references": rerefs,
        }
        qa.write_qa_json(qa_path, self.name, checks, extra)
        outputs.append(qa_path)
        if missing_sets:
            msg = f"components need epoch sets {missing_sets}, which the epochs stage did not write"
            return StageResult("failed", outputs, checks, message=msg)
        return StageResult("ok", outputs, checks, extra={"n_trials": counts})

    def _figure(
        self, ctx: StageContext, set_name: str, cid: str, rc: Any, evokeds: dict[str, mne.Evoked]
    ) -> Path:
        cfg = ctx.config
        d = rc.definition
        conds: dict[str, mne.Evoked]
        diff = None
        if d.contrast:
            c = cfg.contrasts[d.contrast]
            conds = {w: evokeds[w] for w in c.parents}
            diff = (d.contrast, evokeds[d.contrast])
        else:
            conds = {n: ev for n, ev in evokeds.items() if n in cfg.conditions}
        png = ctx.output(
            description=f"{set_name}{cid.title().replace('_', '')}", suffix="erp", extension=".png"
        )
        with atomic_path(png) as tmp:
            component_figure(
                tmp,
                title=f"{d.name}, sub-{ctx.subject}, {rc.key}",
                conditions=conds,
                difference=diff,
                channels=list(d.rois.primary),
                window_ms=(float(d.window_ms[0]), float(d.window_ms[1])),
                lowpass_hz=cfg.erp.plot_lowpass_hz,
            )
        return png
