"""preproc stage (Phase 2): canonical rawdata -> ``desc-preproc_eeg.fif``.

Outputs in ``derivatives/eegunify-preproc/sub-*/[ses-*/]eeg/``:

- ``*_desc-preproc_eeg.fif``: filtered, bads interpolated, re-referenced (float32). Not written
  when QA fails, so nothing downstream can read it.
- ``*_desc-preproc_channels.tsv``: every channel with ``status`` (good/bad), ``reason`` and
  ``interpolated``.
- ``*_desc-preproc_qa.json``: the stage QA (IDs in ``erp/qa.py``) and the steps applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pandas as pd

from eegunify.config.schema import Config
from eegunify.erp import qa
from eegunify.erp.components.library import resolve
from eegunify.erp.preprocess import is_reference_free, load_rawdata, preprocess
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    register_stage,
)


def primary_rois(cfg: Config, project_root: Path) -> dict[str, list[str]]:
    """Component id -> primary ROI channels of the components the config uses."""
    if not cfg.components.use:
        return {}
    res = resolve(cfg.components, project_root=project_root)
    return {cid: list(rc.definition.rois.primary) for cid, rc in res.components.items()}


def channels_table(raw: Any, bads: dict[str, str], interpolated: list[str]) -> pd.DataFrame:
    types = raw.get_channel_types()
    return pd.DataFrame(
        {
            "name": raw.ch_names,
            "type": [t.upper() for t in types],
            "status": [
                "bad" if (c in bads and c not in interpolated) else "good" for c in raw.ch_names
            ],
            "reason": [bads.get(c, "n/a") for c in raw.ch_names],
            "interpolated": [c in interpolated for c in raw.ch_names],
        }
    )


@register_stage
class PreprocStage(Stage):
    name = "preproc"
    schema_version = 2  # 2: bipolar EOG channels derived here (were in ica)

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        return {
            "preproc": cfg.preproc,
            "eog_bipolar": cfg.artifacts.eog_bipolar,
            "reference": {"scheme": cfg.reference.scheme, "channels": cfg.reference.channels},
            # Only QA (PREPROC_ROI_INTERPOLATED) depends on this, but the QA file is an output.
            "primary_rois": primary_rois(cfg, project_root),
        }

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        assert ctx.subject is not None
        loaded = load_rawdata(ctx.project_root, ctx.config, ctx.subject)
        ref_free = is_reference_free(loaded.sidecar)
        res = preprocess(
            loaded.raw,
            ctx.config,
            reference_free=ref_free,
            roi_channels=primary_rois(ctx.config, ctx.project_root),
        )
        outputs = []
        if not res.failed:
            fif = ctx.output(description="preproc", suffix="eeg", extension=".fif")
            with atomic_path(fif) as tmp:
                res.raw.save(tmp, fmt="single", overwrite=True, verbose=False)
            outputs.append(fif)
        tsv = ctx.output(description="preproc", suffix="channels", extension=".tsv")
        with atomic_path(tsv) as tmp:
            channels_table(res.raw, res.bads, res.interpolated).to_csv(
                tmp, sep="\t", index=False, na_rep="n/a", encoding="utf-8"
            )
        qa_path = ctx.output(description="preproc", suffix="qa", extension=".json")
        extra = {
            "subject": ctx.subject,
            "steps": res.steps,
            "online_reference": loaded.sidecar.get("EEGReference"),
            "reference_free": ref_free,
        }
        qa.write_qa_json(qa_path, self.name, res.qa, extra)
        outputs += [tsv, qa_path]
        status: Literal["ok", "failed"] = "failed" if res.failed else "ok"
        failing = [c.message for c in res.qa if c.level == "fail"]
        return StageResult(
            status, outputs, res.qa, message="; ".join(failing), extra={"steps": res.steps}
        )
