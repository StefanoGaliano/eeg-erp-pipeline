# CLAUDE.md
EEG-to-ERP pipeline. Phase 1 = `eegunify`: converts any vendor EEG format to BIDS-EEG (BrainVision payload, µV) with a QA report. Spec: docs/BLUEPRINT.md (source of truth). After Python Phase 1, a MATLAB implementation with matching outputs (Phase M, blueprint section 22).

## Commands
- Env: `conda activate eegunify` (create: `conda env create -f environment.yml`), or `uv sync --extra dev`
- Tests: `pytest -m "not realdata"` (fast); `pytest -m realdata` (needs data)
- Lint/type: `ruff check . && ruff format --check . && mypy`
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
