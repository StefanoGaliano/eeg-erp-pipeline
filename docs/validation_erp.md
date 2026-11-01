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

## M-E5 ERPLAB cross-check (2026-11-01)

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

Bipolar EOG moved from the ica stage to preproc (first step, before filtering and the
re-reference), so HEOG exists without ICA and the cross-check also runs ERPLAB's `pop_artstep`.
On sub-015 nothing measured changed (same 4 ICA exclusions, 13/320 rejected, face − car
−1.22 µV); the VEOG-Fp1 residual after ICA went from r = −0.06 to −0.01 because VEOG is now
Fp2 − VEOG_lower in the recorded reference rather than after the average reference.

Numeric cross-check, `tests/integration/test_erplab_crosscheck.py` (MATLAB R2026b, ERPLAB 13.10).
eegunify's `desc-preproc` data for sub-015 (the `erplab` recipe: the dataset config without ICA)
go through ERPLAB `pop_epochbin`, `pop_artmwppth`, `pop_artstep` (HEOG), `pop_averager`,
`pop_binoperator` and `pop_geterpvalues` (`meanbl`, PO8, 110-150 ms):

| Check | ERPLAB | eegunify |
|---|---|---|
| rejected epochs (face + car) | 56 of 160 | the same 56 events |
| accepted trials face / car | 50 / 54 | 50 / 54 |
| mean amplitude face | 3.951467 µV | 3.951467 µV |
| mean amplitude car | 5.557152 µV | 5.557152 µV |
| face − car | −1.605684 µV | −1.605685 µV |

|Δ| < 1e-6 µV (the 6-decimal rounding of `measures.tsv`); the target was 0.05 µV. Without ICA
the blinks stay in the data, so 35 % of these epochs fail the 100 µV peak-to-peak test (4 % with
the ERP CORE recipe above); for the cross-check only agreement matters.

Two harness fixes were needed to run it: `eeglab nogui` adds only each plugin's top folder and
does not call `eegplugin_erplab`, so `matlab/erplab_crosscheck.m` adds ERPLAB's subfolders itself
(keeping EEGLAB's `icadefs` first); and ERPLAB's epoching drops `urevent`, so rejected epochs are
mapped back to events through `EEG.EVENTLIST.eventinfo(k).bepoch`.

## M-E6a ERP CORE published values (2026-11-01)

`eegunify fetch erpcore-values` (`tests/golden/erpcore_reference/`). Included subjects per
component (from `<component>_Subject_Summary.xlsx`; the measurement files hold exactly these):

| Component | Included | BIDS-archive subjects (015-019) excluded by ERP CORE |
|---|---|---|
| N170 | 37 / 40 | sub-016 (TC: fewer than 50 % of face trials left after artifact rejection) |
| P3 | 34 / 40 | none |
| N2pc | 35 / 40 | none |

ERP CORE's N170 face − car mean amplitude for sub-019 is positive, so the comparison checks that
our sign equals theirs instead of "all negative" (BLUEPRINT_PHASES section 0 and 8.2).

## M-E6a N170 sub-015..019 vs ERP CORE (2026-11-01)

ERP CORE recipe (`code/configs/erpcore_n170.yaml`) with ERP CORE's correct-response bins
(`response: response/correct` within 200-1000 ms, from their `BDF_N170.txt`
`.{1-40}{t<200-1000>201}`; ERPLAB binlister semantics: any 201 in the window, edges inclusive).
Test `tests/integration/test_erpcore_comparison.py`; primary ROI PO8, 110-150 ms.

| Subject | face − car ours | ERP CORE | Δ | status |
|---|---|---|---|---|
| 015 | −0.926 µV | −1.024 µV | +0.10 | ok |
| 016 | — | — | — | excluded by ERP CORE (TC) and by eegunify (`EPOCHS_MIN_TRIALS`: 2 face epochs left) |
| 017 | −2.192 µV | −2.874 µV | +0.68 | **out of tolerance (±0.5)** |
| 018 | −0.885 µV | −0.644 µV | −0.24 | ok |
| 019 | +2.483 µV | +2.041 µV | +0.44 | ok (sign equal to ERP CORE's, positive) |
| mean of 4 | −0.380 µV | −0.625 µV | +0.25 | ok (±0.3) |

Signs match for all four compared subjects. 50 % area latency: within ±8 ms for 015/017/018
(019 has no published value: no negative area). Peak latency: 019 differs by 11.7 ms (ours on the
unfiltered ERP, ERP CORE's on the 20 Hz low-passed one). Parent waves: faces within ±0.5 µV for
3/4 subjects; cars lower than ERP CORE's in 015, 017, 019 (−0.58 to −0.85 µV; mean −0.44 µV).

Remaining recipe differences, all published by ERP CORE per subject and not adopted yet:
artifact-rejection thresholds (`AR_Parameters_for_*_N170.xlsx`: moving-window peak-to-peak on
EEG and on the uncorrected VEOG for blinks, step functions on HEOG), interpolated channels
(`Interpolate_Channels_N170.xlsx`), the ICA components they removed (`ICA_Components_N170.xlsx`,
EEGLAB runica vs our picard + ICLabel), and the 20 Hz low-pass before peak measures.

With the correct-response rule, the ERPLAB cross-check (M-E5) still agrees exactly on sub-015:
17 of 160 face/car trials have no correct response, the same 49 epochs are rejected and the mean
amplitudes are equal to 1e-6 µV.
