# ERP phases: validation on real data

Results of the `realdata` tests for the ERP stages (`pytest -m realdata`), recorded per milestone.
Synthetic ground-truth tests run in CI; see `docs/BLUEPRINT_PHASES.md` section 8.

## M-E1 preproc (2026-10-31)

ERP CORE N170 sub-015, `code/configs/erpcore_n170.yaml` (ERP CORE recipe: 256 Hz, 0.1 Hz non-causal
Butterworth high-pass at 12 dB/oct, no low-pass, F8 interpolated, average reference), test
`tests/integration/test_erp_preproc.py`:

| Check | Result |
|---|---|
| preproc stage runs | ok; a second run is skipped (cache) |
| sampling rate, high-pass recorded | 256 Hz, 0.1 Hz |
| interpolated channels | F8 (bad in ERP CORE's own `channels.tsv`) |
| average reference | 30 EEG channels sum to < 1e-9 V at every sample |
| `REREF_REQUIRED` with `reference.scheme: none` | fail, as required for BioSemi CMS/DRL data |

Open point: the ERP CORE paper references the N170 to the average of "all 33 sites" (30 scalp
channels plus the three EOG electrodes), whereas this pipeline keeps EOG channels out of the
average reference (blueprint rule). The effect on the N170 measure will be checked in M-E4/M-E6a.
