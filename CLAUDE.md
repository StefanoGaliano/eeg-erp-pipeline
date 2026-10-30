# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

EEG-to-ERP pipeline, visual-task focus. Phase 1 = `eegunify`: converts any vendor EEG format into one
canonical BIDS-EEG form (BrainVision files, float32, µV) that every later stage consumes.
**`docs/BLUEPRINT.md` (Phase 1) and `docs/BLUEPRINT_PHASES.md` (ERP phases, A2, Phase M, BCI) are the source of truth** for the architecture, milestones (M0–M5) and later phases
(ERP phases, Phase M = MATLAB parity, Phase A = Streamlit app).
`docs/validation_datasets.md` + `code/configs/validation_datasets.yaml` list 40 real open datasets for testing.

## Commands

Environment: conda env `eegunify` (`conda env create -f environment.yml`). CI uses `uv sync --locked --extra dev`.
If `conda activate` isn't available in the shell, call the binaries directly: `~/miniconda3/envs/eegunify/bin/<tool>`.

- All tests: `pytest -m "not realdata"`
- One test: `pytest tests/unit/test_roundtrip.py::test_convert_to_bids -k bdf`
- Coverage: `pytest --cov=eegunify`
- Lint/format/types: `ruff check . && ruff format --check . && mypy` (mypy is strict; config in pyproject)
- Try on a file: `eegunify inspect <file>`; convert a dataset: `eegunify convert <config.yaml> [-s 001] [--overwrite] [--root .]`
- QA only, no BIDS write: `eegunify qa <config.yaml>`; skip QA on convert with `--no-qa`
- `eegunify config init <file> --out code/configs/x.yaml` (draft), `eegunify config validate <yaml>`, `eegunify config schema`
- Real data: `pytest -m realdata` (ERP CORE golden + cross-check; data cached in `EEGUNIFY_DATA` or `~/.cache/eegunify`); refresh golden files with `--update-golden` after reviewing the diff
- Fetch: `eegunify fetch erpcore -s 015`, `eegunify fetch openneuro ds005841 -s 001`
- Validation harness: `eegunify validate-datasets [--max-mb N] [--only dsXXXX] [--dry-run]` → `derivatives/validation/summary.{tsv,md}`; results written up in `docs/validation_results.md`
- Export for MATLAB: `eegunify export <config> --to eeglab`; `matlab/load_canonical.m` (EEGLAB via bva-io, else FieldTrip readers). MATLAB tests: `pytest -m realdata` with `EEGUNIFY_MATLAB_SETUP=<.../setup_eeg_toolboxes.m>` and `matlab` on PATH (skipped otherwise, e.g. CI)
- Config reference: `eegunify config schema --markdown --out docs/config_reference.md` (a test fails if stale; every field needs a docstring)
- README figure: `python code/scripts/erp_preview.py` (needs converted ERP CORE sub-015)
- App: `eegunify app [--root DIR]` (Streamlit; tests in `tests/unit/test_app.py` use `AppTest`, no browser)

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

App (`app/main.py`, Phase A): three pages selected by a sidebar radio (a radio, not `st.navigation`,
so `AppTest` can drive it). Pages hold no logic: drafting is in `config/draft.py` (shared with
`config init`), validation in `config/loader.parse_config`, and running in `pipeline.convert`. To move
between pages, set `session_state["nav"]`; `main()` applies it before the radio is drawn, because a
widget's value can't change after it renders.

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
- Pre-commit pins ruff/mypy. Keep their `rev:` in sync with the versions in `uv.lock`, or the hooks and CI
  will disagree.
