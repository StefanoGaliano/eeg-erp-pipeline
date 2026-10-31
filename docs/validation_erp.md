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

## M-E2 ica (2026-10-31)

ERP CORE N170 sub-015, same recipe plus `artifacts.method: ica` (picard as extended infomax,
seed 97, fitted on a 1-100 Hz average-referenced copy; ICLabel `eye blink` >= 0.8; EOG z >= 3 on
bipolar VEOG = Fp2 - VEOG_lower and HEOG = HEOG_left - HEOG_right), test
`tests/integration/test_erp_ica.py`:

| Check | Result |
|---|---|
| ica stage runs | ok in ~6 s; a second run is skipped (cache) |
| components | 18 (`n_components: 0.99`) |
| excluded | 4: three ICLabel `eye blink` (p 1.00, 0.91, 0.99; the first also VEOG z = -3.1) and one HEOG z = -3.3 component that ICLabel calls `muscle artifact` (horizontal eye movements) |
| `ICA_BLINK_RESIDUAL` | VEOG-Fp1 r = -0.06 after cleaning (warn above 0.3) |
| `ICA_BRAIN_REMOVED` | none |

Indices differ across OS/BLAS, so the test asserts classes and counts only. ERP CORE's own
pipeline removed blink and eye-movement components with ICA too; whether our set matches theirs
shows up in the N170 comparison (M-E6a).

## M-E3 epochs (2026-10-31)

ERP CORE N170 sub-015, set `stim` (`stimulus/*`, -200 to 800 ms, baseline -200 to 0 ms), rejection
with the blueprint defaults (ERPLAB semantics: moving-window peak-to-peak 100 µV, 200 ms window,
100 ms step, on EEG; step function 32 µV on bipolar HEOG, 200 ms window, 50 ms step), test
`tests/integration/test_erp_epochs.py`:

| Check | Result |
|---|---|
| events logged | 320, 80 per stimulus trial type (= `events.expected_counts`), each with its rawdata code |
| rejected | 13 (4 %): 8 by peak-to-peak, 5 by the HEOG step function (after the M-E5 fixes; before them 5 and 8) |
| kept | face 77, car 78, scrambled_face 77, scrambled_car 75 |
| QA | `EPOCHS_REJECTED_FRACTION`, `EPOCHS_MIN_TRIALS`, `EPOCHS_BASELINE_DRIFT` pass |

ERP CORE set its rejection thresholds per subject; their values are taken from their files in
M-E6a. Exact parity of the rejected set with ERPLAB is the M-E5 test (window placement and
half-up rounding of ms to samples are the points to check there).

## M-E4 erp + measure (2026-10-31)

ERP CORE N170 sub-015, test `tests/integration/test_erp_measures.py`:

| Check | Result |
|---|---|
| preview recipe (`eegunify.erp.recipes.PREVIEW`: 0.1-30 Hz FIR, F8 left bad, average reference, no ICA, ±150 µV whole-epoch) | face − car at PO8, 110-150 ms = **−2.2396 µV**; golden −2.24 ± 0.05 (`tests/golden/erpcore_n170_sub-015/erp/preview_recipe.json`). Faces 3.37 µV and cars 5.61 µV, identical to the v0.1.0 preview script |
| ERP CORE recipe (M-E1 to M-E3 settings) | face − car **−1.22 µV** (SME 0.76 µV), 50 % area latency 144.5 ms; faces 4.42 µV (77 trials), cars 5.64 µV (78). Values after the M-E5 fixes; before them −1.28 µV, 143.5 ms |
| QA | `ERP_NOISE_SME` and `ERP_COMPONENT_POLARITY` pass; `MEASURE_NO_LOCAL_PEAK` warns for the condition waves (they rise through the window; the face − car difference has a local peak) |

The two recipes differ by ICA, interpolating F8, 256 Hz and the 30 Hz low-pass, so different
values are expected. Which one is closer to ERP CORE's published value for sub-015 is checked in
M-E6a against their files.

Synthetic: the noiseless difference wave matches the injected one to 1e-6 µV and its mean
amplitude exactly; through all stages (with noise, ICA and the average reference) the measured
mean amplitude is within 3 SME + 0.2 µV of the injected value. The blueprint's "0.3 µV RMS over
the whole difference wave" is not reachable at the synthetic noise level (~28 trials per
condition give ~0.8 µV RMS of noise), so the bound is stated in SME units instead.

## M-E5 ERPLAB cross-check (in progress, 2026-10-31)

ERPLAB 13.10 (`ucdavis/erplab` release zip, sha256 `5b1c9d21…53fb9f3`) and bva-io 1.74
(`sccn/bva-io` tag tarball, sha256 `e54a4395…77813a0c`) are installed in
`Practice/toolboxes/eeglab/plugins`. Reading their source showed three places where eegunify did
not follow ERPLAB; all are fixed, with tests:

| Rule | ERPLAB 13.10 | eegunify before | now |
|---|---|---|---|
| ms -> samples for rejection windows (`pop_artmwppth`, `pop_artstep`) | `floor(ms*fs/1000)` | half-up rounding (100 ms at 256 Hz: 26 instead of 25) | floor |
| step function: first half of the window | `round(winpnts/2)` samples (MATLAB round) | `winpnts // 2` | MATLAB round |
| fractional area latency (`areaerp`) | trapezoid rule, single precision, binary search for a sample | rectangle bins, interpolated inside a sample | ported |

Matching already: moving-window placement (`p1:stepnts:p2-(winpnts-1)`, peak-to-peak `>`
threshold), the local-peak rule (`localpeak`: more extreme than both adjacent samples and both
`Neighborhood` means; `geterpvalues` passes `Neighborhood` extra samples on each side of the
window), window snapping to the closest samples, and the inclusive mean amplitude.

Remaining differences, by design: ERPLAB refuses a rejection window longer than the epoch (here:
one whole-epoch window, as MNE's `reject`); with no local peak ERPLAB returns NaN unless
`Peakreplace` is `abs` (here: the absolute peak, flagged `MEASURE_NO_LOCAL_PEAK`).

The numeric cross-check in MATLAB (same filtered, re-referenced data through ERPLAB
`pop_epochbin`, `pop_artmwppth`, `pop_averager`, `pop_geterpvalues`; target |Δ| < 0.05 µV and
identical rejected epochs) is not run yet.
