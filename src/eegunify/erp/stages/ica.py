"""ica stage (Phase 3): ``desc-preproc_eeg.fif`` -> ``desc-clean_eeg.fif``.

Outputs in ``derivatives/eegunify-ica/sub-*/[ses-*/]eeg/``:

- ``*_desc-ica_ica.fif``: the fitted ``mne.preprocessing.ICA`` (``exclude`` set).
- ``*_desc-ica_components.tsv``: one row per component with its ICLabel class and the 7
  probabilities, the EOG z-scores, ``excluded`` and the ``reason``.
- ``*_desc-clean_eeg.fif``: the preprocessed data with the excluded components removed. Not
  written when QA fails.
- ``*_desc-ica_qa.json``: the stage QA (IDs in ``erp/qa.py``) and the steps applied.

``artifacts.method: none`` disables the stage; the epochs stage then reads ``desc-preproc``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import mne

from eegunify.config.schema import Config
from eegunify.erp import qa
from eegunify.erp.ica import run_ica
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    register_stage,
)


def preproc_file(inputs: StageInputs) -> Path:
    assert inputs.upstream is not None
    for p in inputs.upstream.outputs:
        if p.name.endswith("_desc-preproc_eeg.fif"):
            return p
    raise FileNotFoundError("the preproc stage wrote no desc-preproc_eeg.fif")


@register_stage
class IcaStage(Stage):
    name = "ica"
    schema_version = 1

    def enabled(self, cfg: Config) -> bool:
        return cfg.artifacts.method == "ica"

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        # The reference is re-applied after ICA, so it is part of this stage's output too.
        return {
            "artifacts": cfg.artifacts,
            "reference": {"scheme": cfg.reference.scheme, "channels": cfg.reference.channels},
        }

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        assert ctx.subject is not None
        raw = mne.io.read_raw_fif(preproc_file(inputs), preload=True, verbose=False)
        res = run_ica(raw, ctx.config.artifacts, ctx.config.reference)
        checks = qa.ica_checks(res.raw, res.decision)
        failed = qa.worst(checks) == "fail"
        outputs = []
        ica_path = ctx.output(description="ica", suffix="ica", extension=".fif")
        with atomic_path(ica_path) as tmp:
            res.ica.save(tmp, overwrite=True, verbose=False)
        tsv = ctx.output(description="ica", suffix="components", extension=".tsv")
        with atomic_path(tsv) as tmp:
            res.decision.table.to_csv(tmp, sep="\t", index=False, na_rep="n/a", encoding="utf-8")
        outputs += [ica_path, tsv]
        if not failed:
            fif = ctx.output(description="clean", suffix="eeg", extension=".fif")
            with atomic_path(fif) as tmp:
                res.raw.save(tmp, fmt="single", overwrite=True, verbose=False)
            outputs.append(fif)
        qa_path = ctx.output(description="ica", suffix="qa", extension=".json")
        qa.write_qa_json(qa_path, self.name, checks, {"subject": ctx.subject, "steps": res.steps})
        outputs.append(qa_path)
        status: Literal["ok", "failed"] = "failed" if failed else "ok"
        failing = [c.message for c in checks if c.level == "fail"]
        extra = {"excluded": res.decision.excluded, "n_components": len(res.decision.table)}
        return StageResult(status, outputs, checks, message="; ".join(failing), extra=extra)
