# eeg-erp-pipeline

[![CI](https://github.com/StefanoGaliano/eeg-erp-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/StefanoGaliano/eeg-erp-pipeline/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

A single pipeline from **any raw EEG recording to ERPs**, focused on visual paradigms (VEP, P1/N1, N170, P3).

**Phase 1 (in progress): `eegunify`**. It converts vendor formats (BrainVision, BioSemi BDF, EDF, EEGLAB,
LSL XDF, GDF, ANT, Neuroscan, Curry, EGI, CSV) into one canonical BIDS-EEG representation. Every later
step reads only that representation. Each converted recording also gets a QA report.

> Work in progress: see [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md) for the architecture and roadmap.
> A native MATLAB (EEGLAB/FieldTrip) implementation with matching outputs is planned after the Python one.

## Install (development)

```bash
conda env create -f environment.yml
conda activate eegunify
eegunify version
pytest
```

## Repository layout

| Folder | Contents |
|---|---|
| `src/eegunify/` | Python package |
| `tests/` | unit, synthetic round-trip and real-data tests |
| `code/configs/` | per-dataset YAML configs |
| `matlab/` | MATLAB implementation (planned) |
| `sourcedata/`, `rawdata/`, `derivatives/` | BIDS data folders (git-ignored) |

## License

Code: MIT. Documentation: CC-BY-4.0.
