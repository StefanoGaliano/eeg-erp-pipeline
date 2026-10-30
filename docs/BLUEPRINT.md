# EEG-to-ERP Pipeline: Architecture Blueprint (Phase 1: Unified Ingestion)

Project codename: **`eegunify`** (Python package), repo name `eeg-erp-pipeline`.
Author: Stefano Galiano. Blueprint date: 2026-10-30.
Audience: a Claude Code instance with zero prior context, building Phase 1 autonomously. Later phases are sketched only.

---

## 0. Decisions (resolved 2026-10-30)

All open questions are resolved. Q1 was decided by the user; Q2–Q7 use the recommended defaults.

| # | Question | Decision |
|---|----------|---------------------|
| Q1 | **Stack:** Python/MNE-Python as the core, or MATLAB/EEGLAB+FieldTrip? | **DECIDED: Python 3.12 + MNE-Python + MNE-BIDS first (Phase 1). Then a native MATLAB implementation with matching outputs (Phase M, section 22).** |
| Q2 | **Canonical on-disk payload** inside BIDS: BrainVision, EDF, or FIF? | **BIDS-EEG with BrainVision payload** (`.vhdr/.vmrk/.eeg`, float32 µV). |
| Q3 | Public dataset for the headline demo: ERP CORE N170 (visual faces) or ERP CORE P3 (visual oddball)? | **ERP CORE N170** (subject 1 for CI-adjacent demo, 3-5 subjects for the README figure). |
| Q4 | License for the repo | **MIT** for code; docs CC-BY-4.0. |
| Q5 | Package/repo name | `eegunify` package, `eeg-erp-pipeline` repo. |
| Q6 | Does the user have real lab recordings in Tier-2/3 formats (ANT, Curry, EGI .mff) to test privately? | Assume **no**; those formats are validated with MNE testing data only, and marked "tested on public samples" in the support matrix. |
| Q7 | Environment manager: conda (miniconda exists) or `uv`? | **conda/mamba `environment.yml`** for local dev (user already has miniconda), **`uv` + `pyproject.toml`** in CI. Both install the same pinned package set. |

---

## 1. Goal and scope

**Overall goal:** one pipeline from ANY raw EEG recording format to ERPs, focused on visual paradigms (VEP, P1/N1, N170, visual P300).

**Phase 1 (this blueprint, fully specified):** the unification/ingestion layer. Input: an original vendor recording (+ optional behavioral log + optional electrode file) in `sourcedata/`. Output: a validated BIDS-EEG recording in `rawdata/` in one canonical representation, plus a per-recording QA report in `derivatives/eegunify-qa/`. Source files are never modified.

**Out of scope for Phase 1:** filtering, re-referencing, ICA, epoching, averaging, measurement. Sketched in section 14 with the contract they consume.

**Non-goals:** real-time/online processing (relevant later for BCI, not now), a GUI, MEG.

---

## 2. Stack decision (Q1)

**Decision: Python 3.12 + MNE-Python + MNE-BIDS as the core. MATLAB is supported by interop, not as the implementation language.**

| Criterion | Python / MNE | MATLAB / EEGLAB + FieldTrip |
|---|---|---|
| Reader coverage for the required formats | Native readers for all Tier 1-2 formats (BrainVision, BDF, EDF/EDF+, GDF, EEGLAB, EGI .mff/.raw, ANT .cnt, Neuroscan .cnt, Curry, XDF via `pyxdf`) | Comparable via plugins (bva-io, biosig, mffmatlabio, loadcurry, xdfimport), but plugin-dependent and less uniform |
| BIDS conversion | MNE-BIDS `write_raw_bids` is the reference implementation, used by OpenNeuro | bids-matlab-tools / FieldTrip `data2bids` exist but are less used |
| Testing / CI | pytest + GitHub Actions free, headless, fast | MATLAB in CI needs licenses (`matlab-actions` works for public repos, but is slower and less standard) |
| Portfolio value | High: reproducible, pip-installable, matches industry and BCI tooling (MNE, MOABB, Braindecode, LSL) | Good inside academia, less visible to reviewers who cannot run MATLAB |
| User familiarity | New (MNE not installed yet) | Has done ex01-ex03 |
| BCI future | Direct path to `pylsl`, MOABB, scikit-learn, PyTorch | Weaker |

**Interop with MATLAB:** `eegunify export --to eeglab` writes `.set/.fdt` (via `mne.export.export_raw`, needs `eeglabio`) into `derivatives/eegunify-eeglab/`. Plus a `matlab/load_canonical.m` helper that loads a canonical BrainVision file into EEGLAB (`pop_loadbv`) or FieldTrip (`ft_preprocessing` with `cfg.dataset`) and reads `events.tsv`. This lets the user cross-check ERPs in MATLAB, which itself becomes a portfolio point ("validated against EEGLAB").

---

## 3. Canonical format decision (Q2)

**Decision: BIDS-EEG (spec 1.10) with BrainVision payload, float32, data stored in µV.**

- **Two layers of canonical:**
  1. **In memory:** `mne.io.Raw` with a strict set of invariants (section 5). Every reader returns this; every downstream stage consumes this.
  2. **On disk:** BIDS-EEG in `rawdata/`, BrainVision payload, plus sidecars (`_eeg.json`, `_channels.tsv`, `_events.tsv`/`.json`, `_electrodes.tsv`, `_coordsystem.json`, `scans.tsv`, `participants.tsv`, `dataset_description.json`).
- **Why BrainVision:** BIDS-approved, readable by MNE, EEGLAB, FieldTrip, Brainstorm and BrainVision Analyzer; supports float32 (no 16/24-bit quantization loss like EDF); events in a sidecar text marker file; explicit units per channel.
- **Why not EDF:** fixed digital range per channel causes quantization/clipping when converting float data; 16-bit only (BDF is 24-bit but non-standard for BIDS EEG). Accept EDF as input, not as canonical.
- **Why not FIF:** not BIDS-valid for EEG, MNE-specific. FIF is used for *derivatives* in later phases (`_proc-*_eeg.fif`, `_epo.fif`, `_ave.fif`) where speed matters.
- **Events are authoritative in `_events.tsv`** (onset s, duration, sample, `value` = original code, `trial_type` = condition name, plus behavioral columns when merged). The `.vmrk` mirrors them.

---

## 4. Format support tiers

| Tier | Format | Extension(s) | MNE reader | Event source | Notes / gotchas |
|---|---|---|---|---|---|
| 1 | BrainVision | .vhdr/.vmrk/.eeg | `read_raw_brainvision` | marker file -> annotations | Marker strings like `Stimulus/S  1`; parse to int code |
| 1 | BioSemi | .bdf | `read_raw_bdf` | `Status` stim channel | Reference-free: online ref = CMS/DRL, mark `EEGReference: "CMS/DRL (reference-free)"`. Mask Status with `0xFF` (lower 8 bits = triggers; upper bits = CMS/battery). Channel names A1..B32 map to 10-20 via cap layout (biosemi64 montage). |
| 1 | EDF / EDF+ | .edf | `read_raw_edf` | EDF+ annotations or a stim channel | Labels like `EEG Fp1-Ref`; units from header; possible clipping |
| 1 | EEGLAB | .set/.fdt | `read_raw_eeglab` | EEG.event -> annotations | Positions may be inside .set |
| 1 | LSL XDF | .xdf | custom via `pyxdf` | separate marker stream | Multi-stream; clock sync and jitter; choose EEG stream by type/name (config) |
| 2 | GDF (g.tec) | .gdf | `read_raw_gdf` | event table | GDF 1 vs 2 |
| 2 | ANT Neuro | .cnt/.evt | `read_raw_ant` (needs `antio`) | embedded or .evt | |
| 2 | Neuroscan | .cnt | `read_raw_cnt` | embedded | 16 vs 32-bit ambiguity: config `data_format` hint |
| 2 | Curry | .cdt/.dap/.rs3 | `read_raw_curry` | embedded | |
| 2 | EGI | .mff folder / .raw | `read_raw_egi` | event tracks | .mff is a directory |
| 3 | Plain CSV/TXT (OpenBCI) | .csv/.txt | custom | marker column or none | Config must declare sfreq, columns, units (OpenBCI GUI exports µV) |
| aux | PsychoPy | .csv | custom | merged into events | |
| aux | Presentation | .log | custom | merged | Tab-separated with header block |
| aux | E-Prime | .txt (E-DataAid tab export) | custom | merged | Require tab-delimited export; raw .edat2 unsupported |
| aux | Electrodes | .elc/.sfp/.bvct/standard names | `read_custom_montage`, `read_dig_captrak`, `make_standard_montage` | | |

Format detection: extension first (with `.cnt` disambiguation by header magic bytes: ANT starts with `RIFF`/`RF64`, Neuroscan with version string), override by `format:` in config.

---

## 5. The canonical in-memory contract (`CanonicalRaw` invariants)

Every reader output passes `eegunify.canonical.validate(raw)` before writing. Invariants:

1. Data in **Volts** in memory (MNE convention); written as **µV** on disk. Unit sanity checks (section 9) prevent mV/V/µV mix-ups.
2. **Channel names** normalized to 10-05 standard case (`Fp1`, `Oz`, `PO7`, `TP9`); see section 7.
3. **Channel types** set for every channel: `eeg`, `eog`, `emg`, `ecg`, `misc`, `stim`, `resp`, `temperature`. Stim channels are dropped after event extraction (events live in annotations).
4. **Events** as `raw.annotations` with description = `"<trial_type>"` and an extras map to original code; zero-duration unless declared.
5. **Online reference** recorded in `raw.info["description"]`-adjacent metadata and in `_eeg.json` `EEGReference`. Never re-referenced in Phase 1 (only BioSemi: no reference applied, data kept as recorded).
6. **Montage** set when positions are known; channels without positions listed in QA.
7. **Bad channels** from config copied to `raw.info["bads"]` and `channels.tsv` `status=bad` with `status_description`.
8. **Measurement date** preserved or anonymized deterministically (config `anonymize: {daysback: ...}`).
9. **Provenance** dict (section 10) attached and written to `_eeg.json` under a `"eegunify"` key... note: BIDS allows extra keys, validator warns only. Also written in full to `derivatives/eegunify-qa/.../_provenance.json`.
10. No resampling, filtering or cropping in Phase 1 (except XDF: resampled onto a uniform grid only if the stream's effective rate deviates > 0.1 % from nominal; logged).

---

## 6. Repository layout

Refinements to the existing skeleton: keep `sourcedata/`, `rawdata/`, `derivatives/`, `docs/`. **Rename the intent of `code/`:** it holds user-level analysis scripts and per-study configs (BIDS convention), while the installable package lives in `src/`. Add `tests/`, `configs/` is folded into `code/configs/`.

```
EEG_Project/                      # git repo root
├── README.md
├── LICENSE
├── CITATION.cff
├── pyproject.toml                # hatchling build, deps, ruff, mypy, pytest config
├── environment.yml               # conda dev env (python=3.12, pip -e .[dev])
├── .gitignore
├── .pre-commit-config.yaml       # ruff, ruff-format, mypy, end-of-file, check-yaml
├── .github/workflows/
│   ├── ci.yml                    # lint + typecheck + unit tests (matrix)
│   └── integration.yml           # weekly + manual: real public data, BIDS validator
├── src/eegunify/
│   ├── __init__.py               # __version__
│   ├── cli.py                    # Typer app
│   ├── config/
│   │   ├── schema.py             # pydantic v2 models (section 8)
│   │   └── loader.py             # YAML load, defaults, env expansion
│   ├── detect.py                 # format detection
│   ├── readers/
│   │   ├── base.py               # Reader protocol + registry decorator
│   │   ├── brainvision.py  biosemi.py  edf.py  eeglab.py  xdf.py
│   │   ├── gdf.py  ant.py  neuroscan.py  curry.py  egi.py
│   │   └── csv_generic.py        # OpenBCI + generic delimited
│   ├── events/
│   │   ├── extract.py            # stim channel / annotations / marker stream -> Events table
│   │   ├── mapping.py            # code -> trial_type, ranges, bitmasks
│   │   └── behavior.py           # PsychoPy / Presentation / E-Prime parsers + alignment
│   ├── channels/
│   │   ├── naming.py             # normalization + alias tables
│   │   ├── aliases.yaml          # T3->T7, T4->T8, T5->P7, T6->P8 etc.
│   │   ├── types.py              # type inference rules
│   │   └── montage.py            # positions: standard / file / embedded
│   ├── units.py                  # unit parsing + sanity heuristics
│   ├── canonical.py              # validate(raw) invariants
│   ├── provenance.py             # hashes, versions, config snapshot
│   ├── bids_writer.py            # MNE-BIDS wrapper + sidecar enrichment
│   ├── qa/
│   │   ├── checks.py             # returns QAResult list (pass/warn/fail)
│   │   ├── plots.py              # PSD, channel layout, event raster, 10 s trace
│   │   └── report.py             # mne.Report HTML + JSON summary
│   ├── export.py                 # EEGLAB .set export
│   ├── datasets.py               # fetchers for public data (pooch)
│   └── rois.py                   # visual ROIs (section 7.3)
├── matlab/
│   └── load_canonical.m          # EEGLAB/FieldTrip loader helper
├── code/
│   ├── configs/
│   │   ├── erpcore_n170.yaml
│   │   ├── erpcore_p3.yaml
│   │   └── example_biosemi_lab.yaml
│   └── scripts/                  # thin study scripts calling the CLI
├── tests/
│   ├── conftest.py               # fixtures, synthetic generators, markers
│   ├── synthetic/                # generators that write each format
│   ├── golden/                   # tiny committed expected sidecars (tsv/json only)
│   ├── unit/
│   └── integration/
├── docs/
│   ├── BLUEPRINT.md              # this file
│   ├── formats.md                # support matrix + per-format gotchas
│   ├── config_reference.md       # generated from pydantic schema
│   ├── canonical_contract.md     # section 5, for downstream users
│   └── img/                      # README figures (small PNGs, committed)
├── sourcedata/ rawdata/ derivatives/   # gitignored content, .gitkeep + README.md each
```

---

## 7. Channels

### 7.1 Name normalization pipeline (`channels/naming.py`)
Applied in order, every step logged (original -> new) into `channels.tsv` column `original_name`... (MNE-BIDS writes channels.tsv; add the column post-write in `bids_writer.py`).
1. Strip whitespace, vendor prefixes/suffixes via regex: `^(EEG|EOG|EMG|ECG)\s+`, `-(Ref|REF|A1|A2|M1|M2|LE|AVG|Cz)$`.
2. Case-normalize against MNE `standard_1005` names (case-insensitive lookup, return canonical case: `FP1`->`Fp1`, `OZ`->`Oz`, `POZ`->`POz`).
3. Apply `aliases.yaml` (old 10-20: `T3->T7, T4->T8, T5->P7, T6->P8`; `A1/A2 -> M1/M2` optional, default off with warning; per-config extra aliases).
4. Cap-layout mapping for positional names (BioSemi `A1..D32` -> 10-20 using a bundled `biosemi64.yaml`/`biosemi128.yaml` mapping; EGI `E1..E129` keep names, use GSN-HydroCel montage).
5. Collisions (two channels mapping to same name) -> hard error naming both originals.

### 7.2 Type inference (`channels/types.py`)
Config `channel_types` wins; else regex rules: `(?i)eog|heog|veog|lo[12]|io[12]` -> eog, `(?i)emg` -> emg, `(?i)ecg|ekg` -> ecg, `(?i)status|trig|sti` -> stim, BioSemi `EXG1-8` -> misc unless configured (typical: EXG1/2 mastoids as eeg-misc, EXG3-6 eog). Name in standard_1005 -> eeg. Else misc + QA warning.

### 7.3 Visual ROIs (`rois.py`) - used by QA and later phases
```yaml
occipital:           [O1, Oz, O2]
parieto_occipital:   [PO7, PO3, POz, PO4, PO8]
lateral_occipitotemporal_left:  [P7, PO7]   # N170 left
lateral_occipitotemporal_right: [P8, PO8]   # N170 right
centro_parietal:     [Cz, CPz, Pz]          # P300
```
QA flags if any `occipital` or `lateral_occipitotemporal_*` channel is missing or bad (warn), since the project is visual-focused.

---

## 8. Per-dataset config schema (YAML, validated with pydantic v2)

One file per dataset (study), with optional per-recording overrides. Example `code/configs/erpcore_n170.yaml`:

```yaml
schema_version: 1
dataset:
  name: "ERP CORE N170"
  bids_root: rawdata             # relative to project root
  source_root: sourcedata/erpcore_n170
  task: faceperception           # BIDS task label
  authors: ["Kappenman ES", "Luck SJ"]
  license: CC-BY-4.0
  anonymize: {daysback: 40000}   # or null

recordings:                      # explicit list OR glob + regex
  pattern: "sub-{subject}/eeg/sub-{subject}_task-N170_eeg.set"
  subjects: ["001", "002", "003"]
  session: null
  run: null

format:
  name: auto                     # auto | brainvision | bdf | edf | eeglab | xdf | gdf | ant | neuroscan | curry | egi | csv
  hints:                         # format-specific, all optional
    neuroscan_data_format: auto  # int16 | int32 | auto
    bdf_status_mask: 0xFF
    xdf_eeg_stream: {type: EEG}  # or {name: "..."}
    xdf_marker_stream: {type: Markers}
    csv: {sfreq: 250, time_column: null, channel_columns: "1:9", unit: uV, delimiter: ",", skiprows: 4, marker_column: null}

units:
  declared: auto                 # auto | V | mV | uV  (overrides header if set)
  expected_eeg_range_uV: [0.5, 500]   # robust SD range for sanity check

reference:
  online: "CMS/DRL"              # free text -> EEGReference; e.g. "Cz", "linked mastoids", "FCz"
  ground: "AFz"

channels:
  rename: {}                     # explicit original -> new, applied after normalization
  types: {HEOG_left: eog, HEOG_right: eog, VEOG_lower: eog}
  drop: []
  bads: {"sub-002": ["T8"]}      # per-subject
  aliases_extra: {}

montage:
  source: standard               # standard | file | embedded | none
  standard_name: standard_1005   # any mne.channels.get_builtin_montages() name
  file: null                     # .elc/.sfp/.bvct, path relative to source_root; may use {subject}
  on_missing: warn               # raise | warn | ignore

events:
  source: auto                   # auto | stim_channel | annotations | marker_stream | marker_file
  stim_channel: null             # e.g. Status
  mapping:                       # code -> trial_type. Ranges and lists allowed.
    "1-40":   stimulus/face
    "41-80":  stimulus/car
    "101-140": stimulus/scrambled_face
    "141-180": stimulus/scrambled_car
    "201": response/correct
    "202": response/error
  unmapped: keep_as_code         # keep_as_code | drop | error
  ignore_codes: [0]
  min_duration_samples: 1        # debounce for stim channels
  expected_counts: {stimulus/face: 40}   # optional, QA compares (tolerance below)
  count_tolerance: 0.05

behavior:                        # optional
  source: psychopy               # psychopy | presentation | eprime | none
  file: "beh/sub-{subject}_*.csv"
  align_on: {eeg_trial_types: ["stimulus/*"], beh_filter: "trial_type != 'practice'"}
  columns: [stim_file, response, rt]   # appended to events.tsv
  max_drift_ms: 20               # QA fails if residual onset mismatch exceeds

overrides:                       # per-subject patches of any key above
  "sub-003":
    channels: {bads: [O2]}
```

Schema rules for the builder:
- All models `extra="forbid"`; errors show the YAML path.
- Mapping keys: single int, `"a-b"` inclusive range, or string label (for annotation-based formats: `"Stimulus/S  1"` and also matched after whitespace collapse).
- Hierarchical trial types with `/` (MNE epoch selection convention).
- `eegunify config schema` dumps JSON Schema; `docs/config_reference.md` generated from it.

---

## 9. QA report per converted recording

Output: `derivatives/eegunify-qa/sub-XX/[ses-YY/]eeg/sub-XX_..._qa.html` (mne.Report) + `_qa.json` (machine-readable) + `_provenance.json`. A dataset-level `derivatives/eegunify-qa/summary.tsv` (one row per recording, status column).

Contents:
1. **Header:** source path, format detected, sha256 of source files, eegunify/MNE/MNE-BIDS versions, config hash, overall status PASS/WARN/FAIL.
2. **Recording summary:** sfreq, duration, n_samples, n_channels by type, meas_date (or anonymized), online reference, power line frequency (config/autodetect via PSD peak at 50 vs 60 Hz).
3. **Channel table:** original name, canonical name, type, unit, position present (y/n), status, robust SD (µV), flat/clipped flags.
4. **Event table:** per trial_type count, expected count, delta, first/last onset; unmapped codes listed; inter-event-interval histogram.
5. **Unit sanity checks:** per-EEG-channel robust SD (MAD*1.4826) after 1 Hz high-pass on a copy; median across channels must be in `expected_eeg_range_uV`. Heuristic diagnosis: median ~1e-6x expected -> "data looks like V labelled as µV"; ~1e3x -> mV confusion. FAIL on out-of-range by >10x, WARN otherwise.
6. **Flat/noisy channels:** SD < 0.5 µV (flat), > 200 µV (noisy), > 0.1 % samples at digital rails (clipping).
7. **Plots:** PSD (Welch, 1-80 Hz, per-type, log scale, line-noise and alpha-band markers; occipital ROI highlighted, since visual data should show posterior alpha ~8-12 Hz), sensor layout (2D topomap positions, missing positions in red), event raster over time, 10 s raw trace of visual ROI channels.
8. **Behavior alignment** (if configured): matched/unmatched trials, onset residual distribution, max drift.
9. **BIDS validation result** (if `bids-validator` available on PATH; else "skipped").

`_qa.json` schema: `{status, checks: [{id, level, message, value, threshold}], counts, sfreq, n_channels, versions}`. Check IDs stable (e.g. `UNITS_RANGE`, `EVENTS_EXPECTED_COUNT`, `ROI_MISSING`) so tests assert on them.

---

## 10. Provenance and immutability

- `sourcedata/` is read-only by contract: the converter opens sources read-only; a test asserts sha256 unchanged after conversion. Optional `eegunify protect sourcedata/` does `chmod -R a-w`.
- Provenance JSON: source files + sha256 + size, detected format, reader function, all MNE/numpy/eegunify versions, git commit of repo (if clean; flag "dirty"), resolved config (after overrides), channel rename log, unit conversion factor, event extraction method, timestamp UTC, host platform.
- `sourcedata/README.md` documents where each dataset came from.
- `rawdata/` is regenerable; `eegunify convert` refuses to overwrite unless `--overwrite`.

---

## 11. CLI (Typer; entry point `eegunify`)

```
eegunify inspect PATH [--format X]                 # print detected format, channels, sfreq, events summary; no writes
eegunify config init PATH --out code/configs/x.yaml # draft a config from a recording (channel types guessed, event codes listed)
eegunify config validate CONFIG
eegunify config schema [--out FILE]
eegunify convert CONFIG [--subject 001 ...] [--overwrite] [--no-qa] [-j N]
eegunify qa CONFIG [--subject ...]                 # rerun QA on existing rawdata
eegunify validate-bids [BIDS_ROOT]                 # wraps bids-validator if installed
eegunify export CONFIG --to eeglab                 # derivatives/eegunify-eeglab/
eegunify fetch {erpcore-n170, erpcore-p3, mne-testing, mne-sample} [--dest sourcedata/...]
eegunify protect sourcedata/
```
Exit codes: 0 ok, 1 QA FAIL in any recording, 2 config/usage error, 3 reader error. Logging via `logging` + `rich` handler; `--verbose/--quiet`.

Python API mirrors CLI: `eegunify.read(path, config=None) -> mne.io.Raw` (canonical), `eegunify.convert(config_path, subjects=None)`, `eegunify.qa.run(raw, config) -> QAResult`.

---

## 12. Data acquisition and .gitignore

**.gitignore** (key lines):
```
sourcedata/**
!sourcedata/.gitkeep
!sourcedata/README.md
rawdata/**
!rawdata/.gitkeep
!rawdata/README.md
derivatives/**
!derivatives/.gitkeep
!derivatives/README.md
*.eeg *.vhdr *.vmrk *.bdf *.edf *.gdf *.set *.fdt *.cnt *.cdt *.mff *.raw *.xdf *.fif
!tests/golden/**
.pooch_cache/
__pycache__/ .venv/ .mypy_cache/ .ruff_cache/ .pytest_cache/ htmlcov/ .coverage
```
Plus a pre-commit hook `check-added-large-files --maxkb=500`.

**Public datasets (`eegunify fetch`, implemented with `pooch`, hashes pinned in `datasets.py`):**
| Dataset | Use | Source |
|---|---|---|
| MNE testing data (`mne.datasets.testing`) | Real-file reader tests for every Tier 1-2 format | MNE |
| ERP CORE N170 (BIDS version, EEGLAB .set inside) | Headline demo, end-to-end, visual | OSF project for ERP CORE (Kappenman et al. 2021); builder looks up the current BIDS download URL on osf.io and pins sha256 |
| ERP CORE P3 (visual oddball) | Second demo, P300 | same |
| MNE sample (`mne.datasets.sample`, FIF with visual stimuli) | Optional extra visual demo | MNE |
| One OpenNeuro visual dataset (e.g. a BioSemi .bdf VEP set) | BDF real-world test, via `openneuro-py` single-subject download | OpenNeuro; builder selects, pins accession |

Data downloads go to `sourcedata/<dataset>/` for demos and to the `~/.cache/eegunify` (`EEGUNIFY_DATA`) for tests.

---

## 13. Testing strategy

Frameworks: pytest, pytest-cov, hypothesis (event mapping / naming), pytest-xdist. Markers: `unit` (default, fast, no network), `realdata` (needs fetched data, skipped if absent), `slow`.

1. **Synthetic round-trip per format (core of the suite).** `tests/synthetic/generate.py` builds a known signal: 32 standard channels incl. O1/O2/Oz/PO7/PO8/P7/P8, 2 EOG, sfreq 512 Hz, 60 s; each channel = known sine (unique freq per channel) + 10 µV alpha on occipital channels + a template "P1/N1" bump (+5/-8 µV at 100/170 ms) after each of 100 events with codes {1: face, 2: car}. Writers per format:
   - BrainVision, EDF, BDF (via `mne.export.export_raw` / `pybv` / `edfio`), EEGLAB (`eeglabio`), GDF (skip writer if none; use MNE testing file), XDF (write with a minimal XDF writer in tests using `pyxdf`-compatible chunk format, or `liesl`/hand-rolled; include a marker stream with 3 ms jitter and clock offset), CSV (numpy), BDF with Status channel having upper-bit noise.
   - Assertions after `read -> canonical -> write BIDS -> read_raw_bids`: channel names/types equal expected; amplitude max abs error < quantization bound (EDF: 1 LSB of its range; float formats: < 1e-3 µV); event onsets within 1 sample (XDF: within 2 ms after sync); trial_type counts exact; units = µV in channels.tsv; sfreq exact.
2. **Unit tests:** naming (table-driven: `EEG Fp1-Ref`->`Fp1`, `OZ`->`Oz`, `T3`->`T7`, collisions raise), mapping (ranges, overlaps rejected, hypothesis property: every code maps to at most one trial_type), units heuristics (V/mV/µV detection on scaled synthetic data), config schema (valid/invalid fixtures with error-path assertions), format detection (ANT vs Neuroscan .cnt magic bytes), behavior alignment (synthetic PsychoPy log with dropped trial -> detected).
3. **Real-file reader tests (`realdata`):** every format in MNE testing data reads to a canonical Raw that passes `canonical.validate`.
4. **Golden files:** for ERP CORE N170 sub-001 and one synthetic recording, commit expected `channels.tsv`, `events.tsv` (first 50 rows + counts), `_eeg.json` (with volatile keys stripped) and `_qa.json` check IDs/levels to `tests/golden/`. Test compares normalized output. `pytest --update-golden` regenerates.
5. **Immutability test:** sha256 of sources before/after convert.
6. **CLI tests:** Typer `CliRunner` for each command, exit codes.
7. **BIDS validity:** in integration workflow, run `bids-validator` (Deno version, `deno run -A jsr:@bids/validator`) on output; zero errors required.
Coverage target: >= 85 % on `src/eegunify` (excluding plots).

---

## 14. CI (GitHub Actions)

`ci.yml` (on push/PR):
- job `lint`: ruff check, ruff format --check, mypy --strict on `src/`.
- job `test`: matrix ubuntu-latest + macos-latest + windows-latest x Python 3.11/3.12/3.13 (full matrix on ubuntu, 3.12 only on others); `uv sync --extra dev`; `pytest -m "not realdata" --cov`; upload coverage to Codecov.
- job `testing-data` (ubuntu, 3.12): cache `~/mne_data` keyed by MNE version; `pytest -m realdata -k "not erpcore"`.

`integration.yml` (weekly cron + workflow_dispatch): fetch ERP CORE N170 sub-001 (cached), `eegunify convert code/configs/erpcore_n170.yaml --subject 001`, golden comparison, bids-validator, upload QA HTML as artifact.

Also: `pre-commit.ci` or pre-commit in lint job; Dependabot for actions and pip; README badges (CI, coverage, Python versions, license).

---

## 15. Downstream phases (sketch) and the contract they rely on

All downstream stages read via `mne_bids.read_raw_bids(BIDSPath(root="rawdata", ...))` and rely only on section 5 invariants + `events.tsv` `trial_type` hierarchy. Outputs go to `derivatives/eegunify-<stage>/` as BIDS-derivatives with `desc-` entities and FIF payloads.

| Phase | Stage | Input contract | Output |
|---|---|---|---|
| 2 | Preprocess: line-noise notch/zapline, 0.1-30/40 Hz band-pass (config), bad channel detection (pyprep/RANSAC), interpolation | canonical Raw, montage set, bads | `_desc-filt_eeg.fif` |
| 2 | Re-reference (average or mastoids), BioSemi requires this | online reference known | `_desc-reref_eeg.fif` |
| 3 | ICA (picard, 1 Hz hp copy), ICLabel (mne-icalabel), EOG-correlated removal | EOG channels typed | `_desc-ica_eeg.fif`, `_ica.fif` |
| 4 | Epoching -200..800 ms, baseline -200..0, autoreject | trial_type with `stimulus/` prefix | `_epo.fif` |
| 5 | ERP averaging per condition, difference waves (face - car) | trial_type hierarchy | `_ave.fif` |
| 6 | Measurement: mean amplitude, peak latency in ROI windows (P1 80-130 ms @ O1/O2/Oz; N170 130-200 ms @ P7/P8/PO7/PO8; P3 300-600 ms @ Pz), group stats, comparison with ERP CORE published values | `rois.py` | `measurements.tsv`, figures |
| later | BCI direction: real-time LSL ingest reusing readers/naming | XDF/LSL reader | - |

Phase 2+ acceptance anchor: reproduce ERP CORE N170 grand-average face-car difference at P8 within the published latency range (~130-200 ms).

---

## 16. README / portfolio presentation plan

README sections: one-line pitch; headline figure (N170 grand average from ERP CORE produced end-to-end; plus QA PSD screenshot); "Why" (format chaos in EEG labs); feature list + **format support matrix** with tested-status badges; 60-second quickstart (`conda env create`, `eegunify fetch erpcore-n170`, `eegunify convert ...`, open QA report); architecture diagram (Mermaid: sourcedata -> readers -> canonical -> BIDS -> QA -> downstream); canonical contract link; MATLAB interop section; reproducibility (pinned env, hashes, provenance); testing/CI; roadmap (phases); citation (CITATION.cff, ERP CORE and MNE citations); license.
Also: `docs/` rendered with MkDocs-Material + mkdocstrings on GitHub Pages (Phase 1.5, optional); a short `notebooks/01_quickstart.ipynb` executed in CI with nbmake (optional).

---

## 17. Dependencies (pyproject)

Core: `mne>=1.10`, `mne-bids>=0.16`, `numpy`, `scipy`, `pandas`, `pyyaml`, `pydantic>=2`, `typer`, `rich`, `pooch`, `pybv`, `edfio`, `eeglabio`, `pyxdf`, `antio`, `defusedxml`, `matplotlib`, `h5io` (if needed for reports).
Dev extra: `pytest`, `pytest-cov`, `pytest-xdist`, `hypothesis`, `ruff`, `mypy`, `pandas-stubs`, `types-PyYAML`, `pre-commit`, `openneuro-py`.
Builder must verify exact current versions on PyPI and pin lower bounds; record resolved lock (`uv.lock`) in repo.

---

## 18. CLAUDE.md for the target project (write to EEG_Project/CLAUDE.md)

```markdown
# CLAUDE.md
EEG-to-ERP pipeline. Phase 1 = `eegunify`: converts any vendor EEG format to BIDS-EEG (BrainVision payload, µV) with a QA report. Spec: docs/BLUEPRINT.md (source of truth).

## Commands
- Env: `conda env create -f environment.yml && conda activate eegunify` (or `uv sync --extra dev`)
- Tests: `pytest -m "not realdata"` (fast); `pytest -m realdata` (needs data)
- Lint/type: `ruff check . && ruff format --check . && mypy src`
- Run: `eegunify convert code/configs/erpcore_n170.yaml --subject 001`

## Rules
- NEVER modify or write into sourcedata/. NEVER commit EEG data (sourcedata/rawdata/derivatives are gitignored).
- In-memory data is Volts (MNE); on-disk BIDS payload is µV float32.
- Every reader returns a Raw passing `eegunify.canonical.validate`. New formats: add reader in src/eegunify/readers/, register it, add a synthetic round-trip test and a row in docs/formats.md.
- No filtering/re-referencing/resampling in Phase 1.
- Config models are pydantic with extra="forbid"; update docs/config_reference.md via `eegunify config schema`.
- QA check IDs are stable API; don't rename without updating golden files.
- Type hints everywhere, mypy strict, numpy-style docstrings.
- Visual focus: default ROIs in src/eegunify/rois.py.
```

---

## 19. Build order (Phase 1) — the builder follows these steps in sequence

1. **Repo bootstrap:** `git init` in EEG_Project; write `.gitignore`, `LICENSE` (MIT), `pyproject.toml`, `environment.yml`, `.pre-commit-config.yaml`, README stub, CLAUDE.md (section 18), `README.md` in sourcedata/rawdata/derivatives; create `src/eegunify/` skeleton, `tests/`, `code/configs/`, `matlab/`. Keep `code/.gitkeep` replaced by `code/configs/`. Acceptance: `pip install -e .[dev]` works, `eegunify --help` runs, `pytest` runs (0 tests), pre-commit passes.
2. **CI skeleton:** `ci.yml` lint + test jobs green on an empty test suite.
3. **Config schema + loader** (section 8) with unit tests on valid/invalid fixtures; `config validate` and `config schema` commands.
4. **Channels:** naming, aliases.yaml, type inference, BioSemi layout mapping; table-driven + hypothesis tests.
5. **Units module + canonical.validate** with tests.
6. **Synthetic generator** (section 13.1) writing BrainVision/EDF/BDF/EEGLAB/CSV.
7. **Reader protocol + registry + detect.py**; Tier 1 readers: BrainVision, BDF (Status masking), EDF, EEGLAB. Round-trip tests each.
8. **Events:** extraction from stim channel / annotations, mapping with ranges, unmapped policy; tests.
9. **Montage module** (standard/file/embedded) with `.elc/.sfp/.bvct` tests using MNE testing data.
10. **BIDS writer + provenance**, `original_name` column, refuse-overwrite, immutability test.
11. **`convert` and `inspect` CLI**; end-to-end synthetic test producing a BIDS dataset.
12. **QA:** checks.py (all IDs), plots.py, report.py, summary.tsv; tests assert check IDs on deliberately broken synthetic data (V-as-µV, flat O2, missing PO8, wrong event counts).
13. **XDF reader** (stream selection, clock sync, marker stream -> events) + synthetic XDF test.
14. **Datasets fetcher** (pooch, pinned hashes) + `fetch` CLI; ERP CORE N170 config; integration workflow; golden files generated and committed.
14b. **Real-world validation harness:** `eegunify validate-datasets code/configs/validation_datasets.yaml` downloads one subject per entry (openneuro-py / pooch), converts it, and writes `derivatives/validation/summary.tsv` (dataset, format, convert ok, QA pass/warn/fail counts, error). The catalog is in `docs/validation_datasets.md` (40 datasets, mostly visual). It runs from the weekly integration workflow, never in the fast CI. The README support matrix is generated from its results.
15. **Tier 2 readers** (GDF, ANT, Neuroscan with .cnt disambiguation, Curry, EGI) with `realdata` tests on MNE testing data.
16. **Generic CSV/OpenBCI reader** with config-declared columns.
17. **Behavior module** (PsychoPy, Presentation, E-Prime tab export) + alignment + QA section; synthetic tests.
18. **EEGLAB export + `matlab/load_canonical.m`**.
19. **`config init`** draft generator.
20. **Docs + README:** formats.md, config_reference.md, canonical_contract.md, README with headline figure produced from ERP CORE (a minimal ERP plot script under code/scripts/ is allowed as a preview, labelled "preview of Phase 4-5"), badges.
21. **Release v0.1.0:** tag, CHANGELOG, GitHub release.

## 20. Milestones and acceptance criteria

| Milestone | Steps | Acceptance criteria |
|---|---|---|
| M0 Bootstrap | 1-2 | CI green; package installs; pre-commit clean |
| M1 Tier-1 core | 3-11 | BrainVision/BDF/EDF/EEGLAB synthetic round-trips pass; `eegunify convert` writes BIDS that `read_raw_bids` loads; sources unchanged (hash test) |
| M2 QA | 12 | HTML+JSON report per recording; broken-data fixtures each trigger the right check ID |
| M3 XDF + real data | 13-14 | ERP CORE N170 sub-001 converts with 0 bids-validator errors; event counts match ERP CORE documentation; golden tests pass; integration workflow green |
| M4 Coverage breadth | 15-17 | All Tier 2 + CSV readers pass on MNE testing files; behavior merge works on synthetic logs |
| M5 Portfolio | 18-21 | EEGLAB export opens in EEGLAB via load_canonical.m (manual check by user); README complete with figure and support matrix; coverage >= 85 %; v0.1.0 released |

## 21. Skills for the builder
- `/find-skills` not required. Recommend the builder use `/code-review` before each milestone, `/simplify` after M1, and `/security-review` is not needed (no network services).

---

## 22. Phase M: MATLAB parity (after Python Phase 1 / M5)

The user decided that the same ingestion pipeline must also exist natively in MATLAB. This shows they can use both ecosystems.

- **Package:** `matlab/+eegunify/`, which gives functions such as `eegunify.convert(configPath)`, `eegunify.read_any(path)`, `eegunify.validate(EEG)` and `eegunify.qa(EEG)`.
- **Configs:** the same `code/configs/*.yaml` files the Python code uses. Parse them with a small YAML helper, or have the Python side write a JSON copy (`eegunify config dump --json`) that MATLAB reads with `jsondecode`. The configs must stay one shared source of truth.
- **Readers (EEGLAB):** `pop_loadbv` (BrainVision), `pop_biosig` (BDF/EDF/GDF), `pop_loadset`, `pop_loadxdf` (XDF), `pop_mffimport` (EGI), and `pop_loadcnt`/`loadcurry` (Neuroscan/Curry). Use FieldTrip `ft_read_header`/`ft_read_data` as the fallback.
- **Output:** the same canonical output, BIDS-EEG with BrainVision files in µV as float32, written with bids-matlab-tools `bids_export`. Run the same 10 canonical checks (section 5) as `eegunify.validate`.
- **Environment:** reuse `Practice/setup_eeg_toolboxes.m` and `Practice/toolboxes/{eeglab,fieldtrip}`. Never use `addpath(genpath(...))`.
- **Tests:** `matlab.unittest` in `matlab/tests/`, run locally with `matlab -batch "runtests('matlab/tests')"`. They are not run in CI because CI has no MATLAB license. Note this in the README.
- **Acceptance (parity test):** for ERP CORE N170 sub-001, the MATLAB and Python outputs must match:
  - `events.tsv` must be identical (onset to 1 sample, `value` and `trial_type` exact)
  - `channels.tsv` names and types must be identical
  - channel data must agree to within float32 tolerance (max abs diff < 1e-3 µV)
  - a `tests/integration/test_matlab_parity.py` test compares the two outputs and is skipped if MATLAB is not installed
