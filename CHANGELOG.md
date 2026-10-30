# Changelog

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
