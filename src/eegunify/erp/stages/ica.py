"""ica stage (Phase 3): ``desc-preproc_eeg.fif`` -> ``desc-clean_eeg.fif``.

Outputs in ``derivatives/eegunify-ica/sub-*/[ses-*/]eeg/``:

- ``*_desc-ica_ica.fif``: the fitted ``mne.preprocessing.ICA`` (``exclude`` set); not written
  for ``artifacts.method: precomputed`` (the decomposition is the EEGLAB file).
- ``*_desc-ica_components.tsv``: one row per component with its ICLabel class and the 7
  probabilities, the EOG z-scores, ``excluded`` and the ``reason``.
- ``*_desc-clean_eeg.fif``: the preprocessed data with the excluded components removed. Not
  written when QA fails.
- ``*_desc-ica_qa.json``: the stage QA (IDs in ``erp/qa.py``) and the steps applied.

``artifacts.method: wavelet`` writes ``desc-clean`` from wavelet thresholding instead
(``erp/wavelet.py``; no ICA files, QA ``WAVELET_VARIANCE_KEPT``).
``artifacts.method: none`` disables the stage; the epochs stage then reads ``desc-preproc``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import mne

from eegunify.config.schema import Config
from eegunify.datasets import sha256
from eegunify.erp import qa
from eegunify.erp.ica import IcaResult, run_ica
from eegunify.erp.precomputed_ica import (
    apply_precomputed,
    read_eeglab_decomposition,
    resolve_weights,
)
from eegunify.erp.preprocess import add_bipolar_eog, interpolate
from eegunify.erp.stages.base import (
    Stage,
    StageContext,
    StageInputs,
    StageResult,
    atomic_path,
    register_stage,
)
from eegunify.erp.wavelet import wavelet_clean


def preproc_file(inputs: StageInputs) -> Path:
    assert inputs.upstream is not None
    for p in inputs.upstream.outputs:
        if p.name.endswith("_desc-preproc_eeg.fif"):
            return p
    raise FileNotFoundError("the preproc stage wrote no desc-preproc_eeg.fif")


@register_stage
class IcaStage(Stage):
    name = "ica"
    schema_version = (
        4  # 3: precomputed ICA, corrected bipolars, interpolation after ICA; 4: wavelet
    )

    def enabled(self, cfg: Config) -> bool:
        return cfg.artifacts.method in ("ica", "precomputed", "wavelet")

    def hash_config(self, cfg: Config, project_root: Path) -> Any:
        # The reference is re-applied after ICA, so it is part of this stage's output too.
        out: dict[str, Any] = {
            "artifacts": cfg.artifacts,
            "reference": {"scheme": cfg.reference.scheme, "channels": cfg.reference.channels},
            "interpolate_after_ica": cfg.preproc.interpolate_after_ica,
        }
        if cfg.artifacts.precomputed is not None:
            weights = resolve_weights(cfg.artifacts.precomputed, project_root)
            out["weights_sha256"] = sha256(weights) if weights.exists() else "missing"
        return out

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageResult:
        assert ctx.subject is not None
        cfg = ctx.config
        raw = mne.io.read_raw_fif(preproc_file(inputs), preload=True, verbose=False)
        if cfg.artifacts.method == "wavelet":
            return self._wavelet(ctx, raw)
        if cfg.artifacts.method == "precomputed":
            assert cfg.artifacts.precomputed is not None
            spec = cfg.artifacts.precomputed
            path = resolve_weights(spec, ctx.project_root)
            decomp = read_eeglab_decomposition(path)
            decision = apply_precomputed(raw, decomp, spec.exclude_eeglab)
            steps: dict[str, Any] = {
                "precomputed": {
                    "weights": path.name,
                    "channels": decomp.channels,
                    "exclude_eeglab": spec.exclude_eeglab,
                    "reference": "unchanged (the data's own)",
                }
            }
            res = IcaResult(raw, None, decision, steps)
        else:
            res = run_ica(raw, cfg.artifacts, cfg.reference)
        if cfg.artifacts.corrected_bipolar:
            res.steps["corrected_bipolar"] = add_bipolar_eog(raw, cfg.artifacts.corrected_bipolar)
        if cfg.preproc.interpolate_after_ica:
            bads = list(raw.info["bads"])
            res.steps["interpolated_after_ica"] = interpolate(raw, bads, True)
        checks = qa.ica_checks(res.raw, res.decision)
        if "fit_exclude" in res.steps:
            checks.append(qa.fit_excluded_check(res.steps["fit_exclude"]))
        failed = qa.worst(checks) == "fail"
        outputs = []
        if res.ica is not None:
            ica_path = ctx.output(description="ica", suffix="ica", extension=".fif")
            with atomic_path(ica_path) as tmp:
                res.ica.save(tmp, overwrite=True, verbose=False)
            outputs.append(ica_path)
        tsv = ctx.output(description="ica", suffix="components", extension=".tsv")
        with atomic_path(tsv) as tmp:
            res.decision.table.to_csv(tmp, sep="\t", index=False, na_rep="n/a", encoding="utf-8")
        outputs.append(tsv)
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

    def _wavelet(self, ctx: StageContext, raw: mne.io.BaseRaw) -> StageResult:
        cfg = ctx.config
        steps = wavelet_clean(raw, cfg.artifacts.wavelet)
        if cfg.artifacts.corrected_bipolar:
            steps["corrected_bipolar"] = add_bipolar_eog(raw, cfg.artifacts.corrected_bipolar)
        if cfg.preproc.interpolate_after_ica:
            steps["interpolated_after_ica"] = interpolate(raw, list(raw.info["bads"]), True)
        checks = [qa.wavelet_check(steps["wavelet"]["variance_kept_pct"])]
        fif = ctx.output(description="clean", suffix="eeg", extension=".fif")
        with atomic_path(fif) as tmp:
            raw.save(tmp, fmt="single", overwrite=True, verbose=False)
        qa_path = ctx.output(description="ica", suffix="qa", extension=".json")
        qa.write_qa_json(qa_path, self.name, checks, {"subject": ctx.subject, "steps": steps})
        return StageResult("ok", [fif, qa_path], checks, extra=steps["wavelet"])
