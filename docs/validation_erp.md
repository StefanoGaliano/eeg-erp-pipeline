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

`eegunify fetch erpcore-values` (`src/eegunify/erp/reference/`). Included subjects per
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

With this automatic recipe sub-017 is a documented deviation (0.68 µV, outside ±0.5); the
realdata test marks it `xfail(strict=True)`. The `erpcore-exact` recipe below closes it, which
shows the gap comes from ERP CORE's per-subject decisions (their ICA components, rejection
thresholds, interpolation), not from an implementation error.

With the correct-response rule, the ERPLAB cross-check (M-E5) still agrees exactly on sub-015:
17 of 160 face/car trials have no correct response, the same 49 epochs are rejected and the mean
amplitudes are equal to 1e-6 µV.

## M-E6a `erpcore-exact` recipe, N170 sub-015..019 (2026-11-01)

`eegunify.erp.recipes.erpcore_exact` (BLUEPRINT_PHASES 8.2a) follows ERP CORE's own processing of
each subject, from their files (`src/eegunify/erp/reference/N170_exact.json`): average
reference over all 33 recorded sites (EOG electrodes included, applied to them too), their
EEGLAB ICA decomposition with their removed components (`*_ica_weighted.set` headers), corrected
bipolar EOG after ICA, interpolation after ICA (015 F8, 017 C6, 018 FP2), their five rejection
tests with per-subject parameters (`pop_artextval` ±200 µV; `pop_artmwppth` on 28 EEG channels at
85-115 µV; blinks on the uncorrected VEOG; two `pop_artstep` on HEOG), and the 20 Hz, 48 dB/oct
low-pass before peak measures.

| Subject | face − car ours | ERP CORE | Δ | status |
|---|---|---|---|---|
| 015 | −1.464 µV | −1.024 µV | −0.44 | ok |
| 016 | — | — | — | excluded by ERP CORE (TC); only 2 correct face trials |
| 017 | −2.390 µV | −2.874 µV | +0.48 | ok |
| 018 | −0.600 µV | −0.644 µV | +0.04 | ok |
| 019 | +2.157 µV | +2.041 µV | +0.12 | ok |
| mean of 4 | −0.574 µV | −0.625 µV | +0.05 | ok |

Latencies: 50 % area latency within one sample (3.9 ms) for 015/017/018; peak latency (now on
the 20 Hz low-passed ERP, as theirs) equal or one sample off for all four (019: 109.4 ms both,
was 11.7 ms off with the default recipe).

Trials per bin vs ERP CORE's `<sub>_AR_Percentages_N170.csv`: the correct-response candidates
are identical for every subject and bin; kept trials are identical for 015, 016, 017 and 019 car,
and differ by 1 (018 face, 019 face) or 3 (018 car).

Parent waves: faces and cars come out lower than ERP CORE's by 0.27 and 0.32 µV on average
(face 015 −0.71 µV, outside ±0.5; car mean −0.32 µV, outside ±0.3). Difference waves cancel it.
A common offset on both conditions points at slow-signal processing that is still not identical:
resampling (MNE vs EEGLAB `pop_resample`), the 0.1 Hz high-pass implementation, and spherical
interpolation (MNE vs EEGLAB). Not pursued further for M-E6a, whose acceptance is on the
difference wave.

## M-E6a group stage, N170 sub-015..019 (2026-11-01)

`eegunify erp run code/configs/erpcore_n170.yaml` now ends with the group stage
(`derivatives/eegunify-group/task-N170/`); the realdata comparison test reads its
`task-N170_erpcore-comparison.tsv`. sub-016 is excluded by our `EPOCHS_MIN_TRIALS` (2 correct
face trials; ERP CORE excluded it too), so N = 4 (`GROUP_SUBJECTS_EXCLUDED` warn).

| | default recipe | erpcore-exact |
|---|---|---|
| face − car mean amplitude, mean ± SEM | −0.38 ± 1.00 µV | −0.57 ± 0.98 µV |
| paired t (= one-sample on face − car), p | t = −0.38, p = 0.73 | t = −0.59, p = 0.60 |
| Wilcoxon p | 0.875 | 0.625 |
| d_z | −0.19 | −0.29 |
| cluster test (0-400 ms, one-tailed negative), best cluster | 160-215 ms, P3/P4/PO3/PO4/PO7/O1/Pz, p = 0.875 | 160-195 ms, P3/P4/PO3/PO7/Pz/C6, p = 0.94 |
| `REFERENCE_VALUES_MISMATCH` | 7/24 compared values outside tolerance (017 face − car; parent waves) | 2/24 (015 face, car mean: the parent-wave offset) |

As expected with 4 subjects, one of whom (019) has a positive face − car difference in ERP
CORE's own data, nothing is significant: `GROUP_UNDERPOWERED` notes that the smallest possible
one-tailed permutation p is 1/16 = 0.0625 > 0.05. No d_z confidence interval is given below 8
subjects (the bootstrap is degenerate: with N = 4 it gave a lower bound of −45). The group
numbers that can be compared with ERP CORE's 40-subject results are M-E6b.

## M-E6a P3 and N2pc, sub-015..019 (2026-11-01)

`code/configs/erpcore_p3.yaml` (rare - frequent at Pz, 300-600 ms) and `erpcore_n2pc.yaml`
(contra - ipsi at PO7/PO8, 200-275 ms, lateralized contrast over ERP CORE's 11 channel pairs),
both referenced to the mean of P9/P10, correct-response bins, 26 ms shift; each run with the
default recipe and with `erpcore-exact` (ERP CORE's per-subject parameters,
`src/eegunify/erp/reference/{P3,N2pc}_exact.json`). ERP CORE excluded none of 015..019 for
these two components. Test `tests/integration/test_erpcore_p3_n2pc.py`.

Contrast mean amplitude, ours - ERP CORE (µV; tolerance ±0.5 per subject, ±0.3 for the mean):

| Subject | P3 default | P3 exact | N2pc default | N2pc exact |
|---|---|---|---|---|
| 015 | +0.01 | +0.00 | +0.03 | −0.09 |
| 016 | excluded by us | −0.08 | +0.06 | −0.11 |
| 017 | +0.28 | −0.04 | −0.22 | +0.10 |
| 018 | +0.35 | +0.04 | +0.03 | +0.05 |
| 019 | **−0.62** | −0.05 | **−1.34** | +0.15, **sign differs** (ours +0.002, theirs −0.151) |
| mean | +0.004 (of 4) | −0.02 | −0.29 | +0.02 |

50 % area latency: within one sample (3.9 ms) everywhere except P3 exact sub-015 (−11.7 ms).
P3 exact parent waves (rare, frequent) are all within ±0.25 µV of ERP CORE's, unlike N170's
(where a 0.3 µV offset remains with the average reference).

### Documented deviations

These are known and accepted; the realdata tests report them (they fail on them) rather than
encode exceptions.

- **P3, default recipe, sub-016 excluded.** One 100 µV peak-to-peak threshold for everyone
  rejects 57 % of its epochs and leaves 13 rare trials (< 30): `EPOCHS_MIN_TRIALS` fails and
  the subject leaves the group. ERP CORE kept it with its own threshold of 160 µV
  (`AR_Parameters_for_MW_CRAP_P3.xlsx`); with `erpcore-exact` it is kept and within 0.08 µV.
- **P3, default recipe, sub-019: −0.62 µV** (outside ±0.5). Within 0.05 µV with
  `erpcore-exact`, so it comes from ERP CORE's per-subject choices (ICA components, thresholds).
- **N2pc, default recipe, sub-019: −1.34 µV.** ERP CORE's N2pc for this subject is almost
  absent (−0.15 µV); ours with the automatic recipe is −1.50 µV. Within 0.15 µV with
  `erpcore-exact`.
- **N2pc, `erpcore-exact`, sub-019: sign differs** (ours +0.002 µV, ERP CORE's −0.151 µV,
  0.15 µV apart). Both values are essentially zero, so the sign rule was refined (BLUEPRINT
  section 0 and 8.2): the sign is checked only when |published| > 0.5 µV. Under the refined
  rule this subject passes; it is no longer a deviation.
- **N170, default recipe, sub-017** (above): 0.68 µV; within 0.48 µV with `erpcore-exact`.

Summary: with ERP CORE's own per-subject choices (`erpcore-exact`) all three components
reproduce their published per-subject contrast amplitudes within ±0.5 µV for every compared
subject (P3 within ±0.08 µV, N2pc within ±0.15 µV); the one sign disagreement, on a near-zero
N2pc value, passes the refined sign rule. The automatic default recipe agrees within tolerance for 10 of the 13
subject-component pairs it measures and excludes one subject ERP CORE kept.

## Phase M2 ERP-lite parity, N170 sub-015 (2026-11-02)

`tests/integration/test_matlab_erp_lite.py`, recipe `erp_lite` (the dataset recipe without
ICA, resampling and interpolation; F8 stays bad and out of the average reference). Python:
`eegunify convert` + ERP stages. MATLAB: `eegunify.convert` (M1) + `eegunify.erp_lite`
(ERPLAB 13.10 `pop_basicfilter`, binlister, `pop_epochbin`, `pop_artmwppth`, `pop_artstep`,
`pop_averager`, `pop_geterpvalues`), from the same config dump (`config_hash` checked).

| Check | Tolerance | Result |
|---|---|---|
| Continuous EEG after filter + re-reference (29 good EEG channels, 10 s edges excluded) | < 0.1 µV | max 0.0149 µV |
| Rejected face/car epochs | identical | identical, 50 of 160 |
| Trials per condition | identical | identical |
| ERP RMS per good EEG channel (face, car, face - car) | < 0.05 µV | worst 0.00046 µV |
| N170 mean amplitude, PO8 (face, car, face - car) | < 0.05 µV | Python 3.5986, 5.2089, -1.6103; MATLAB 3.5957, 5.2047, -1.6090 |

The mean-amplitude difference (up to 0.004 µV) is larger than the waveform difference because
ERPLAB's `window2sample` and eegunify's nearest-sample window can pick windows one sample apart.

**Found by this test:** the Python preproc does not filter EOG channels (`_filter_all` filters
MNE "data" channels and misc; EOG is neither). Recorded and derived EOG keep their DC offset
(HEOG about 13.7 mV in `desc-preproc`), while ERPLAB filters every channel. EEG channels and
everything measured on them are unaffected; the HEOG step rule and the ICA EOG vote see
unfiltered EOG. EOG channels are left out of the M2 comparison until this is decided.
