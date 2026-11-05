# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

EEG-to-ERP pipeline, visual-task focus. Phase 1 = `eegunify`: converts any vendor EEG format into one
canonical BIDS-EEG form (BrainVision files, float32, µV) that every later stage consumes.
**`docs/BLUEPRINT.md` (Phase 1) and `docs/BLUEPRINT_PHASES.md` (ERP phases, A2, Phase M, BCI) are the source of truth** for the architecture, milestones (M0–M5) and later phases
(ERP phases, Phase M = MATLAB parity, Phase A = Streamlit app).
`docs/validation_datasets.md` + `code/configs/validation_datasets.yaml` list 40 real open datasets for testing.

## Status

Frozen at v0.9.2 (2026-11-02), bug-fix release v0.9.3 (2026-11-05): every blueprint milestone is done. Bug fixes only; no new features
or recipe changes without the user asking. BCI / real-time (BLUEPRINT_PHASES §13) is a separate project.

## Commands

Environment: conda env `eegunify` (`conda env create -f environment.yml`). CI uses `uv sync --locked --extra dev`.
If `conda activate` isn't available in the shell, call the binaries directly: `~/miniconda3/envs/eegunify/bin/<tool>`.

- All tests: `pytest -m "not realdata and not realdata_full"` (the default addopts)
- One test: `pytest tests/unit/test_roundtrip.py::test_convert_to_bids -k bdf`
- Coverage: `pytest --cov=eegunify`
- Lint/format/types: `ruff check . && ruff format --check . && mypy` (mypy is strict; config in pyproject)
- Try on a file: `eegunify inspect <file>`; convert a dataset: `eegunify convert <config.yaml> [-s 001] [--overwrite] [--root .]`
- QA only, no BIDS write: `eegunify qa <config.yaml>`; skip QA on convert with `--no-qa`
- `eegunify config init <file> --out code/configs/x.yaml` (draft), `eegunify config validate <yaml>`, `eegunify config schema`
- `eegunify config dump <yaml> --json --resolved [-s 015] [--out f.json]`: full config + resolved components + `config_hash` (Phase M reads it with `jsondecode`; `config/dump.py`; its `matlab` block holds mappings as lists of pairs, because jsondecode mangles keys like `"1-40"`, plus the 10-05/alias/BioSemi tables)
- Phase M (MATLAB): `matlab/+eegunify/` (`convert(dump.json, 'root', R, 'bids_root', OUT)`, `read_any`, `normalize_names`, `infer_type`, `extract_events`, `canonical_validate`, `write_bids`). `erp_lite(vhdr, dump.json, outDir)` (M2; config with the `erp_lite` recipe; ERPLAB core shared with M-E5 in `erplab_erp.m`, path setup in `erplab_path.m`). Parity: `pytest -m realdata tests/integration/test_matlab_parity.py tests/integration/test_matlab_erp_lite.py` (ERP CORE `.set` + synthetic BrainVision/BDF; needs `EEGUNIFY_MATLAB_SETUP`). Keep MATLAB steps in the same order as `pipeline.read_recording`; MATLAB `round` is half-away, Python's half-even (`round_half_even` in `extract_events.m`)
- Real data: `pytest -m realdata` (ERP CORE golden + cross-check; data cached in `EEGUNIFY_DATA` or `~/.cache/eegunify`); refresh golden files with `--update-golden` after reviewing the diff
- ERP CORE N170, all 40 subjects (M-E6b): `eegunify fetch erpcore-full -c N170` (3.9 GB, OSF pfde9), config `erpcore_n170_full.yaml`
  (`recordings.pattern` with `{subject_int}`), `pytest -m realdata_full` (skips unless all 40 are fetched)
- Fetch: `eegunify fetch erpcore -s 015`, `eegunify fetch openneuro ds005841 -s 001`
- ERP CORE published per-subject values: `eegunify fetch erpcore-values [-c N170] --dest ~/.cache/eegunify/erpcore`
  (`reference_values.py`) rewrites `src/eegunify/erp/reference/<comp>{,_subjects}.tsv`; a realdata test checks them against OSF
- Validation harness: `eegunify validate-datasets [--max-mb N] [--only dsXXXX] [--dry-run]` → `derivatives/validation/summary.{tsv,md}`; results written up in `docs/validation_results.md`
- Export for MATLAB: `eegunify export <config> --to eeglab`; `matlab/load_canonical.m` (EEGLAB via bva-io, else FieldTrip readers). MATLAB tests: `pytest -m realdata` with `EEGUNIFY_MATLAB_SETUP=<.../setup_eeg_toolboxes.m>` and `matlab` on PATH (skipped otherwise, e.g. CI)
- Config reference: `eegunify config schema --markdown --out docs/config_reference.md` (a test fails if stale; every field needs a docstring)
- README figures: `python code/scripts/erp_preview.py` (needs converted ERP CORE sub-015);
  `python code/scripts/erpcore_grand_average.py --root N170=. --root P3=. --root N2pc=.` (after `erp run` of the three configs)
- App: `eegunify app [--root DIR]` (Streamlit; tests in `tests/unit/test_app.py` use `AppTest`, no browser)

## ERP phases (docs/BLUEPRINT_PHASES.md; all milestones done; frozen at v0.9.2)

- `eegunify erp run <config> [-s ..] [--from/--to/--force <stage>]`; `eegunify erp status <config>` (subject x stage cache
  table); `eegunify erp components [--preset erpcore] [--show n170]` lists the component library.
- Implemented stages: preproc (`erp/preprocess.py` helpers, `erp/stages/preproc.py`), ica (`erp/ica.py`,
  `erp/stages/ica.py`; disabled by `artifacts.method: none`), epochs (`erp/epochs.py`, `erp/stages/epochs.py`; reads
  `desc-clean`, else `desc-preproc`; disabled when `epochs.sets` is empty), erp, group (`erp/group.py`,
  `erp/stages/group.py`; `level = "group"`, outputs in `paths.group_dir`, gets `inputs.subjects`/`inputs.excluded`).
  Group tests run only on contrast mean amplitudes (linear: one-sample on the contrast = paired test);
  no d_z CI below `MIN_BOOTSTRAP_N` = 8 subjects. New built-in stages go in
  `BUILTIN_MODULES` in `erp/stages/base.py` (imported lazily; importing them from `stages/__init__` is circular).
- Filters follow ERPLAB: `iir_order` is the total forward-backward order (2 = 12 dB/oct, half-amplitude cutoff), so MNE
  gets `order // 2`. MNE updates `info["highpass"/"lowpass"]` only when it picks the channels itself: `_filter_all`.
- ICA is fitted and ICLabel-classified on a 1-100 Hz average-referenced copy (ICLabel's training conditions);
  cleaning average-references the data, applies ICA, then re-applies `reference.scheme` (linear, so exact).
  `scheme: none` is refused. picard runs as extended infomax (`ortho=False`). ICA tests check blink removal
  by applying the solution to the blink part alone (`blink_truth`), which is exact because cleaning is linear.
- Epoch rejection is numpy with ERPLAB semantics (`pop_artmwppth`, `pop_artstep`), ms -> samples is `floor`, the step
  function's first half `round(winpnts/2)` with MATLAB rounding; ERPLAB parity is `test_erplab_crosscheck.py`. The per-epoch log lists every selected event, also those MNE drops
  (`TOO_SHORT`, `BAD_`); `code` is matched by onset to rawdata `events.tsv`.
- Tests that run the default stages up to one stage use `Runner(...).run(to="preproc")[0]`: the plan lists
  every stage.
- Synthetic ERP data: `tests/synthetic/erp.py` (`make_erp_recording`); the `erp_project` fixture copies a converted project.
  The background is spatially smooth on purpose: with independent noise per channel pyprep flags almost every channel.
- Real-data ERP results are recorded in `docs/validation_erp.md`.
- `artifacts.method: wavelet` (`erp/wavelet.py`, HAPPE-style, PyWavelets) runs in the ica stage slot and writes `desc-clean`;
  default threshold `universal` (BayesShrink stripped the infant VEP). Not a port of HAPPE (GPL-3).
- Stages: preproc -> ica -> epochs -> erp -> group (`erp/stages/`, subclass `Stage`, `@register_stage`). Outputs go to
  `derivatives/eegunify-<stage>/` (BIDSPath via `erp/paths.py`), cached by `stage_key` (`erp/hashing.py`). The runner
  (`erp/runner.py`) writes the provenance holding the key, so stages only write outputs (via `atomic_path`).
- ERP config sections (`config/erp_sections.py`, imported into `schema.py` for the docs) are all optional; `reference.online`
  describes the recording, `reference.scheme/channels` is the offline re-reference.
- Component windows/ROIs are data: `src/eegunify/erp/components/*.yaml`; never hard-code a window.
- A component's `reference` is applied (`erp/references.py`): if it differs from the data's, the erp stage measures it on
  re-referenced copies (QA `ERP_REFERENCE_CHANGED`); `_ave.fif` keeps the dataset reference. Opt out per component with
  `components.overrides: {id: {reference: null}}` (the `erpcore-exact` recipe does). Synthetic VEP: `make_vep_recording`/`vep_config`.
- Never shift event onsets in ERP stages (Phase 1 already applied `onset_shift_ms`).
- Select events only through `erp.events.select` (MNE tag matching).
- Reference numbers (ERP CORE) only from fetched files or the paper, never typed from memory.
- ERP CORE comparison: sign must equal ERP CORE's per subject when |theirs| > tolerance (their sub-019 N170 is positive;
  near zero the sign is not checked); subjects ERP CORE
  excluded (`*_subjects.tsv`, N170 sub-016) are reported, not checked (BLUEPRINT_PHASES §0, §8.2).
- `erpcore-exact` recipe (`erp.recipes.erpcore_exact`, BLUEPRINT_PHASES §8.2a): ERP CORE's own per-subject choices
  from `src/eegunify/erp/reference/N170_exact.json` + their ICA weights (`erpcore_exact.fetch_exact`, cache only).
  ERPLAB semantics to keep: `window2sample` = `round(t*fs) + round(|xmin|*fs)` clipped; `pop_artextval` bounds
  inclusive; binlister `{t<a-b>code}` = any matching event in [a, b] ms. OSF sometimes answers bursts with 403.
- New QA IDs go in the `erp/qa.py` docstring table; IDs are API.

## Architecture (data flow)

`pipeline.read_recording(path, cfg)` is the single entry point. Every format goes through the same steps:

1. `detect.detect_format` → `readers/<fmt>.py` returns a preloaded `mne.io.Raw` plus format facts
   (`ReadResult`: source files, default reference, status mask, notes). Readers stay small and register
   themselves with `@register`. Everything format-independent happens after them.
2. `channels/naming.normalize_names` builds an original→canonical rename map (stim channels are protected so
   events can still find them). Then `channels/types` infers types. If no channel is recognisably EEG, the
   reader's EEG typing is kept.
3. `events/extract.extract_events` reads the stim channel or the annotations and maps codes to trial types
   (`events/mapping`). It then **replaces `raw.annotations`** (description = trial_type,
   `extras={"code": original}`) and drops the stim channels.
4. Bad channels, montage (`channels/montage`), optional `units.declared` rescale, unit sanity check (`units`).
5. A `canonical.Recording` is built and `canonical.validate` runs the 10 blueprint rules. Error-level
   violations raise `CanonicalError`, so nothing invalid is written.
6. `bids_writer.write_recording` calls MNE-BIDS, then patches the sidecars: `events.tsv` `value` = original
   code, `channels.tsv` `original_name`, `_eeg.json` `EEGReference` plus an `eegunify` block. The full
   provenance goes to `derivatives/eegunify-qa/`.
7. QA (`qa/checks.run_checks`) runs on the in-memory `Recording`, not on the written files. It produces
   `QACheck`s with **stable IDs** (listed in the `qa/checks.py` docstring) plus a channel table.
   `qa/report.py` writes `derivatives/eegunify-qa/sub-*/<basename>_qa.{html,json}` and upserts a row in
   `derivatives/eegunify-qa/summary.tsv`. QA statistics use at most `MAX_SECONDS` of data.

`pipeline.convert` loops over the subjects of a config (after `config.resolve_for_subject` applies
overrides). A failing subject is reported in its `ConvertResult` and doesn't stop the others. The CLI
(`cli.py`) imports heavy modules lazily and maps outcomes to exit codes: 0 ok, 1 recording failed,
2 config/usage, 3 reader error. A QA FAIL also exits with 1.

App (`app/main.py`, Phase A): Phase-1 pages plus the ERP pages of `app/erp_pages.py` (A2) in one sidebar radio (a radio, not `st.navigation`,
so `AppTest` can drive it). Pages hold no logic: drafting is in `config/draft.py` (shared with
`config init`), validation in `config/loader.parse_config`, and running in `pipeline.convert`. To move
between pages, set `session_state["nav"]`; `main()` applies it before the radio is drawn, because a
widget's value can't change after it renders.
ERP pages edit the dataset config as the loaded YAML dict in `session_state["erp_cfg"]` (`get`/`put`/`drop`
with dotted keys; saving writes back only keys that are there), validate it with `parse_config` on every rerun,
read derivatives via `erp/paths.py`, plot only via `erp/plots.py` (functions return figures). Tables absent from
the YAML must show and keep schema defaults (an absent `baseline_ms` is -200..0, not "none").
Tests: `tests/unit/erp/test_app_erp.py` (precomputed project per module; acceptance: UI config == hand config).

Tests: `tests/synthetic/generate.py` writes a signal with known content in each format (`Truth` holds
exact samples, codes and µV data). Round-trip tests compare against it. The `make_project` fixture in
`tests/conftest.py` builds a throwaway project (sourcedata + config).

Data sources (`datasets.py`): ERP CORE files are pulled out of the 1.9 GB OSF zip with HTTP range
requests (`HTTPRangeFile` + `zipfile`), never the whole archive. OpenNeuro subjects are listed through
GraphQL, by snapshot tag. Every download is recorded with its sha256 in `eegunify_manifest.json`; a file
already present with the recorded hash is not fetched again. `validation.py` runs each catalog entry
end to end with an auto-drafted config, and every failure becomes a row in the summary, not a crash.

Event sources (`events/extract.py`, `auto` mode): the reader's `ReadResult.event_source` if set (EGI →
annotations), else stim channel → if empty, file markers → if still empty, the source's BIDS
`*_events.tsv`. `STATUS/<n>` markers are masked like BioSemi Status. Readers can add housekeeping codes
via `ReadResult.ignore_codes` (Curry 50000). Impedance-check markers are dropped as housekeeping.

Behaviour (`events/behavior.py`): logs are aligned by time (offset by consensus of all pair differences,
then nearest match + linear refit for drift) so a dropped trigger can't shift later trials; columns go
into `events.tsv` as `beh_<col>`; QA `BEHAVIOR_ALIGNMENT`. The format support matrix is `docs/formats.md`.

## Rules

- NEVER modify or write into `sourcedata/` (a test checks this with hashes). NEVER commit EEG data: the data
  folders are gitignored, and a pre-commit hook blocks files over 500 KB.
- Units: Volts in memory (MNE), µV on disk. No filtering, re-referencing or resampling in Phase 1.
- New format: add `readers/<fmt>.py`, import it in `readers/__init__.py`, map its extension in
  `detect.py`, add a writer to `tests/synthetic/generate.py` and a round-trip test, plus a row in
  `docs/formats.md`.
- Config models use `extra="forbid"`. A new config key needs the schema field, the pipeline code
  that uses it, and a test.
- Every bug found on real data gets a synthetic regression test named after the dataset that showed it.
- Commit messages: no `Co-Authored-By` trailer.

## Gotchas found the hard way

- `mne.export.export_raw` truncates float annotation onsets (554.9998 → 554) when writing BrainVision.
  Write synthetic BrainVision files with `pybv` and integer event samples. `pybv` takes **Volts**.
- MNE-BIDS ≥ 0.20 writes annotation `extras` as `events.tsv` columns. Using the key `value` clashes with its
  own column, which is why the key is `code`.
- Setting a channel to `misc` drops its Volt unit. `pipeline._channel_types` restores it, so channels such
  as BioSemi EXG are still written in µV.
- MNE 1.13 deprecates `standard_1005`/`standard_1020` in favour of `colin27_*`. Use the fallbacks in
  `channels/naming.py` and `channels/montage.py`.
- QA "clipping" counts only samples stuck at an extreme (equal to the previous sample). Clean sines touch
  their min/max every cycle, so a plain "at the extreme" count gives false positives.
- MNE-BIDS writes one `electrodes.tsv` per subject unless `electrodes_tsv_task=True`, and several tasks per
  subject then collide. `montage.source: none` must also drop embedded positions: positions without
  fiducials can't be written.
- Paths inside a project: use `os.path.abspath`, not `Path.resolve()`, and walk with `os.walk(followlinks=True)`.
  Datasets are often symlinked into `sourcedata/`, and resolving them moves them outside the project root.
- `AppTest` runs the script outside coverage's view, so `app/main.py` is omitted from coverage (see pyproject).
- `pyproject` addopts deselect `realdata`; plain `pytest` never touches the network.
- ERP CORE's own `events.tsv` stores EEGLAB's 1-based latency in `sample`; ours is 0-based (BIDS). The
  golden test asserts `theirs - ours == 1`.
- MNE-BIDS fills `Manufacturer` from the *output* format (BrainVision). `pipeline` copies hardware/task
  keys from a BIDS source's own `_eeg.json` (`INHERITED_SIDECAR_KEYS`) or uses the reader's manufacturer.
- Unit rule 1 refuses a file only when it's > 10x outside the range *and* a 10^3/10^6 factor explains it.
- XDF: `pyxdf.load_xdf(..., dejitter_timestamps=True)` (not `dejitter_timeseries`). The test writer in
  `tests/synthetic/xdf_writer.py` puts the marker stream on another clock (0.5 s) to exercise sync.
- Tier-2 readers need `antio`, `curryreader`, `mffpy` (runtime deps). Real-file tests fetch single files from
  mne-testing-data pinned at `datasets.MNE_TESTING_COMMIT`.
- Neuroscan `.cnt` headers may say 0 samples: `readers/neuroscan._disambiguate` reads int16 and int32 and keeps
  the plausible one (mne-testing `scan41_short.cnt`: 40 µV vs 2.6 V).
- Cross-format tests (`tests/integration/test_formats.py`) are the strongest reader checks: ANT vs its
  BrainVision export, Curry 7/8 vs the BDF original.
- `.txt` maps to the CSV reader (OpenBCI GUI writes `OpenBCI-RAW-*.txt`).
- Config field docs are attribute docstrings (`use_attribute_docstrings=True`); pydantic drops a field
  description identical to its model's class docstring, so word them differently.
- `read_raw_bids` labels events `trial_type/value` (`stimulus/face/12`) when a trial type has several
  codes: select with MNE tag matching (`epochs["stimulus/face"]`), never exact names.
- ERP CORE visual stimuli appear 26 ms after the trigger (LCD delay): `events.onset_shift_ms` in
  `erpcore_n170.yaml`; the ERP CORE cross-check expects stimulus onsets = theirs + 26 ms.
- All text I/O passes `encoding="utf-8"` and relative paths shown or stored use `.as_posix()` (Windows CI;
  an AST test enforces the encoding rule).
- MATLAB subprocesses need `stdin=subprocess.DEVNULL`.
- `eegunify convert` in the repo root makes MNE-BIDS append its references to the tracked `rawdata/README.md`:
  `git checkout -- rawdata/README.md` before committing.
- `eeglab nogui` does not call plugin init functions: ERPLAB's subfolders are not on the path until
  `addpath(genpath(<erplab>))` (done in `matlab/erplab_crosscheck.m`). ERPLAB epochs have no `urevent`;
  use `EEG.EVENTLIST.eventinfo.bepoch`.
- Pre-commit pins ruff/mypy. Keep their `rev:` in sync with the versions in `uv.lock`, or the hooks and CI
  will disagree.
