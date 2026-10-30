# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

EEG-to-ERP pipeline, visual-task focus. Phase 1 = `eegunify`: converts any vendor EEG format into one
canonical BIDS-EEG form (BrainVision files, float32, µV) that every later stage consumes.
**`docs/BLUEPRINT.md` is the source of truth** for the architecture, milestones (M0–M5) and later phases
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
- Pre-commit pins ruff/mypy. Keep their `rev:` in sync with the versions in `uv.lock`, or the hooks and CI
  will disagree.
