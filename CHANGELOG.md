# Changelog

## 0.2.0 (2026-10-31)

**Single-subject ERPs**: the ERP stages on canonical data (docs/BLUEPRINT_PHASES.md, M-E0 to
M-E4).

- **Stage runner:** `eegunify erp run/status`, cached per subject and stage by a key over the
  config section, component definitions, upstream keys and versions; `--from/--to/--force`.
- **preproc:** line noise, ERPLAB-style Butterworth filters, bad channels (config, pyprep, LOF),
  interpolation, re-reference (average, channels, REST); BioSemi data must be re-referenced.
- **ica:** picard (extended infomax) on a 1–100 Hz average-referenced copy, ICLabel plus an EOG
  correlation vote, `_components.tsv` with a reason per component; optional bipolar EOG and ASR.
- **epochs:** epoch sets selected by trial-type prefix, ERPLAB-style moving-window peak-to-peak
  and HEOG step rejection, a per-epoch log with the rawdata code and reason.
- **erp:** averages per condition, difference waves, `measures.tsv` (mean amplitude, peak,
  fractional-area latency) from the component library, one figure per component.
- **QA:** 15 new checks with stable IDs (`erp/qa.py`).
- **Validation:** synthetic ground truth for every stage; ERP CORE N170 sub-015 through all
  stages; the v0.1.0 preview value (−2.24 µV) is a golden regression test.

## 0.1.0 (2026-10-30)

First release: **Phase 1, unified ingestion**. Any supported raw EEG recording becomes one
canonical BIDS-EEG form (BrainVision, float32, µV), with a QA report.

- **Formats:** BrainVision, BioSemi BDF, EDF/EDF+, EEGLAB, LSL XDF, GDF, ANT eego, Neuroscan,
  Curry 7/8, EGI `.mff`, CSV/OpenBCI; events from trigger channels, markers, XDF marker streams
  or BIDS `events.tsv`. See `docs/formats.md`.
- **Canonical checks:** 10 invariants, including unit sanity (µV/mV/V mix-ups refused with the
  fix suggested). See `docs/canonical_contract.md`.
- **Configs:** per-dataset YAML, strictly validated; `config init` drafts one from a recording.
  See `docs/config_reference.md`.
- **QA reports:** HTML + JSON per recording and a dataset `summary.tsv`, with 12 checks that have
  stable IDs and a visual-ROI focus.
- **Behavioural logs:** PsychoPy, Presentation and E-Prime, aligned by time.
- **Interactive app:** `eegunify app` (Streamlit): Inspect → Configure → Convert + QA.
- **MATLAB:** `eegunify export --to eeglab` and `matlab/load_canonical.m`, verified in MATLAB
  against Python.
- **Validation:**
  - ERP CORE N170 golden tests, with events matching the dataset authors' own files.
  - Cross-format tests on vendor files (ANT vs BrainVision, Curry vs BDF).
  - 21 open datasets run with automatic configs: 19 convert and 2 are refused for real defects
    (`docs/validation_results.md`).
