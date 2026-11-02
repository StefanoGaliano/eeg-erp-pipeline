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
| 019 | **−0.55** | −0.05 | **−1.34** | +0.15, **sign differs** (ours +0.002, theirs −0.151) |
| mean | +0.02 (of 4) | −0.02 | −0.29 | +0.02 |

50 % area latency: within one sample (3.9 ms) everywhere except P3 exact sub-015 (−11.7 ms).
P3 exact parent waves (rare, frequent) are all within ±0.25 µV of ERP CORE's, unlike N170's
(where a 0.3 µV offset remains with the average reference).

### Documented deviations

These are known and accepted. The realdata tests list them exactly (`KNOWN_DEFAULT` in
`tests/integration/test_erpcore_p3_n2pc.py`, since 2026-11-02): a new deviation, or a listed
one that disappears, fails the test.

- **P3, default recipe, sub-016 excluded.** One 100 µV peak-to-peak threshold for everyone
  rejects 57 % of its epochs and leaves 13 rare trials (< 30): `EPOCHS_MIN_TRIALS` fails and
  the subject leaves the group. ERP CORE kept it with its own threshold of 160 µV
  (`AR_Parameters_for_MW_CRAP_P3.xlsx`); with `erpcore-exact` it is kept and within 0.08 µV.
- **P3, default recipe, sub-019: −0.55 µV** (outside ±0.5; −0.62 before EOG channels were
  filtered, 2026-11-02; mean of 4 was +0.004). Within 0.05 µV with
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
| Continuous data after filter + re-reference (29 good EEG + 5 EOG channels, 10 s edges excluded) | < 0.1 µV | max 0.0149 µV |
| Rejected face/car epochs | identical | identical, 50 of 160 |
| Trials per condition | identical | identical |
| ERP RMS per good EEG and EOG channel (face, car, face - car) | < 0.05 µV | worst 0.00046 µV |
| N170 mean amplitude, PO8 (face, car, face - car) | < 0.05 µV | Python 3.5986, 5.2089, -1.6103; MATLAB 3.5957, 5.2047, -1.6090 |

The mean-amplitude difference (up to 0.004 µV) is larger than the waveform difference because
ERPLAB's `window2sample` and eegunify's nearest-sample window can pick windows one sample apart.

**Found by this test (fixed 2026-11-02):** the Python preproc did not filter EOG channels
(`_filter_all` filtered MNE "data" channels and misc; EOG is neither), so recorded and derived
EOG kept their DC offset (HEOG about 13.7 mV in `desc-preproc`) while ERPLAB filters every
channel. `_filter_all` now filters EOG, EMG and ECG too (preproc `schema_version` 3; synthetic
regression test `test_erpcore_n170_eog_channels_are_filtered`), and the M2 comparison includes
the EOG channels. Effect on the ICA recipes: on N170 sub-015 and P3 sub-016 the same ICA
components are excluded (EOG z-scores move by < 0.1) and the rejected epochs are identical;
the only comparison value that moved is P3 sub-019, default recipe, from −0.62 to −0.55 µV
(closer to ERP CORE), group mean from +0.004 to +0.02 µV. N2pc sub-019 is unchanged
(−1.34); every other comparison stays within tolerance. The HEOG
step rule compares half-window means, so a DC offset cancels; a 0.1 Hz drift is negligible
over 200 ms.

## VEP on real data: GABA infant pattern-reversal VEP (2026-11-02)

`code/configs/gaba_vep.yaml`: five infants (four 4-month-old, one 10-month-old) from the HAPPE+ER
tutorial files (Zenodo 5172962, CC-BY-4.0; `eegunify fetch zenodo 5172962 --suffix .raw`), EGI
HydroCel 129 channels at 1000 Hz. Measured at E75 (Oz) against E11 (Fz, HAPPE's HydroCel table)
on re-referenced copies of average-referenced data (`ERP_REFERENCE_CHANGED`). Notch 60/120/180
Hz, FIR 0.3-35 Hz, pyprep RANSAC bads (limit raised to 40 %), interpolation, wavelet
thresholding (below; the table is without it, `artifacts.method: none`), epochs
-100..450 ms, one `pop_artextval` rule (-200..200 µV) on HAPPE's tutorial channels E70, E83,
E71, E75, E76.

P100 window: the paediatric presets from Thompson et al. 2023 (Doc Ophthalmol 147:147-164,
Table 5, 95 % limits by age; CC-BY-4.0): `vep_p100@iscev_4mo_large` 90-131, `_4mo_small`
111-170, `_mature_large` 87-115, `_mature_small` 96-131 ms. The files give neither the check
size nor which recording is the 10-month-old, so the config spans all four: 87-170 ms. N75 and
N135 keep the adult ISCEV windows (no infant limits in that paper).

| Subject | Bad channels | Epochs kept | N75 ms | P100 ms | P100 µV | N135 ms | Notes |
|---|---|---|---|---|---|---|---|
| a | 35 % | 93/100 | 60 | 128 | 2.8 | 160 | 32 %, 87/100, 61/129/151 ms, 3.3 µV before 0.7.1 (below) |
| b | 17 % | 98/100 | 76 | 108 | 11.6 | 152 | clear N75/P100 |
| c | 17 % | 94/115 | 76 | 106 | 23.5 | 160 | `MEASURE_NO_LOCAL_PEAK` warn |
| d | 36 % | 79/102 | 88 | 148 | 6.6 | 139 | E75 itself interpolated (`PREPROC_ROI_INTERPOLATED`) |
| e | 12 % | 93/100 | 69 | 107 | 15.9 | 153 | |

With the adult window (90-130 ms) a and d were cut at the window edge (129, 130 ms); with the
paediatric one d's peak is at 148 ms, inside the 4-month small-check limits (111-170).
`VEP_LATENCY_ATYPICAL` keeps the adult 115 ms flag and marks a and d. This checks the VEP chain
on real data, not infant norms (five infants, unknown check size).

Rerun with 0.9.0 (2026-11-02): b-e unchanged. In a, the gross-variance check added in 0.7.1
(`bad_channels.max_variance_ratio`, on by default) flags E44 and E125-E127 (face/eye sites with
large infant eye activity), so 4 more channels are interpolated and a's row changed as above.

**The post-ICA check (`artifacts.post_bad_channels`, 2 x) does not transfer to these infants.**
It runs only after ICA (this config uses wavelet thresholding), so the ratios were computed
offline on the wavelet-cleaned data and compared with HAPPE's own bad channels
(`tutorial_badChannels.xlsx` on the Zenodo record; HAPPE's 10-20 names mapped to E numbers with
its `happe_netdata_lib.mat`; Cz/E129, the flat reference, left out), within the 41 channels
HAPPE analysed (`tutorial_SampleParameters.mat`):

| criterion | matches HAPPE | flagged, not HAPPE's | missed |
|---|---|---|---|
| RANSAC (this config) | 12 | 23 | 38 |
| + 2 x after cleaning | 12 | 25 | 38 |
| + 1.5 x after cleaning | 15 | 32 | 35 |

HAPPE is a weak reference here (it ran its detection on those 42 channels only, and RANSAC
matches 12 of its 50), but the 2 x check adds nothing: no match, two more flags. Over all 129
channels it would flag 1-16 more per infant, mostly face and edge sites (E1, E8, E14, E17, E21,
E119-E127), taking d from 36 % to about 49 % bad, past the 40 % limit. It stays opt-in.

**With wavelet thresholding** (`artifacts.method: wavelet`, the config's setting; `erp/wavelet.py`,
HAPPE's ERP approach with PyWavelets, `coif4`, level 11 at 1000 Hz, soft universal threshold):

| Subject | Variance kept | Pre/post r (HAPPE's QC) | Epochs kept | P100 ms | P100 µV |
|---|---|---|---|---|---|
| a | 26 % | 0.64 (0.79) | 90/100 | 129 | 3.0 |
| b | 43 % | 0.70 (0.91) | 98/100 | 109 | 11.4 |
| c | 40 % | 0.75 (0.69) | 103/115 | 107 | 23.3 |
| d | 26 % | 0.67 (0.67) | 85/102 | 131 | 7.8 |
| e | 48 % | 0.77 (0.79) | 94/100 | 107 | 14.5 |

More epochs survive the -200..200 µV rule; P100 latencies and amplitudes stay close to the
uncorrected ones except d (148 -> 131 ms; E75 interpolated, the noisiest recording). The
BayesShrink threshold (`artifacts.wavelet.threshold: bayesshrink`) was tried first: it kept
only 2-15 % of the variance (pre/post r 0.42-0.57) and shrank the VEP (c 23.5 -> 5.4 µV), so
the universal threshold is the default. HAPPE uses MATLAB's empirical-Bayes `wdenoise`, which
has no Python equivalent; the numbers are close to HAPPE's in spirit, not identical.
Found on this dataset and fixed: single-segment EGI `.raw` files, an EGI event track named
`resp` typed as a respiration channel, the temporary-file path kept in `raw.filenames`, and text
event codes (`vep+`) in the epochs log.

## M-E6b N170, all 40 subjects vs ERP CORE (2026-11-02)

`code/configs/erpcore_n170_full.yaml`: the 40 raw recordings of ERP CORE's N170 OSF project
(pfde9, fetched with `eegunify fetch erpcore-full -c N170`), same recipe as
`erpcore_n170.yaml`. Test `tests/integration/test_erpcore_full.py` (`pytest -m realdata_full`,
about 7 min). Measure: face − car mean amplitude at PO8, compared per subject with ERP CORE's
published values (tolerance ±0.5 µV per subject, ±0.3 µV on the mean). Subjects ERP CORE
excluded (001, 005, 016) are reported, not compared.

| | default recipe | erpcore-exact |
|---|---|---|
| subjects reaching the group stage | 38 | 39 |
| compared subjects (kept by both) | 37 | 37 |
| face − car mean, ours vs ERP CORE | −3.31 vs −3.37 µV | −3.31 vs −3.37 µV |
| d_z, ours vs ERP CORE | −1.20 vs −1.24 | −1.22 vs −1.24 |
| subjects within ±0.5 µV | 21/37 | 34/37 |
| paired t, p (our group stage) | t(37) = −7.41, p = 8.1e-9 | t(38) = −7.85, p = 1.8e-9 |
| peak / 50 % fractional-area latency vs ERP CORE (mean) | −2.4 / −4.1 ms | −2.2 / −4.1 ms |

Default recipe (0.9.0), subjects failing a stage: 001 (epoch rejection) and 016 (2 face epochs
left), both excluded by ERP CORE too; 005, which ERP CORE excluded, passes and is reported, not
compared. The 37 compared subjects are the same as with `erpcore-exact` (where only 016 drops
out). In 0.8.0, 003 and 033 also failed, on channels that no pyprep test flags (see 4 below).

In 0.7.1 the default recipe lost eight subjects (001, 005, 010, 016, 023, 030, 033, 035). The
per-epoch logs gave three causes, each fixed after 0.7.1 by following ERP CORE's own files:

1. **ICA fitted on the breaks.** Sub-035 has a ~35 mV whole-head burst in a 66 s break between
   blocks; it took 93 % of the PCA variance, ICA kept 2 components, no blink component was
   found (VEOG-Fp1 r = 0.72 after cleaning) and the blinks failed the peak-to-peak test.
   ERP CORE's `Script2_ICA_Prep.m` fits ICA without the breaks (>= 2 s between stimulus codes,
   2 s buffers) and without stretches over a peak-to-peak threshold (`ICA_Prep_Values_N170.xlsx`:
   mostly 400 µV, 500 ms windows, 50 ms step). Now `artifacts.ica.fit_exclude` (on in both N170
   configs): the solution is still applied to the whole recording; QA `ICA_FIT_EXCLUDED`.
   Regression test: `test_erpcore_n170_sub035_burst_in_break_left_out_of_fit`.
2. **HEOG step rule stricter than ERP CORE's.** The blueprint default ran a 32 µV step test on
   the HEOG over the whole epoch (187 rejections in 010, 152 in 023; ERP CORE rejected 30 and 39
   epochs in all). ERP CORE uses 32 µV on the uncorrected HEOG in −50..350 ms only, plus 64 µV
   on the ICA-corrected HEOG over the epoch. The default recipe has no corrected HEOG (ICA does
   not clean the EOG channels), and the 64 µV rule on the uncorrected HEOG still rejected 127
   epochs of 010, so the N170 configs use the first rule only (`epochs.reject.method: rules`).
3. **Bad channels.** Against ERP CORE's 27 hand-picked bad channels (40 subjects), pyprep's
   tests on our data:

   | pyprep test | matches ERP CORE | flagged, not ERP CORE's | missed |
   |---|---|---|---|
   | RANSAC (0.7.x default) | 0 | 8 | 27 |
   | deviation | 5 | 0 | 22 |
   | high-frequency noise | 5 | 4 | 22 |
   | correlation | 6 | 59 | 21 |
   | full PREP | 15 | 75 | 12 |

   RANSAC's extra flags were mostly P9/P10/PO3, next to the N170 sites. The full config now uses
   `bad_channels.method: pyprep_robust` (NaN/flat + deviation + HF noise, plus the gross-variance
   check). It catches 030's Fp1/Fp2 and 033's C5/C6; 003's Fp1 and 001's F4 pass every test.

Recovered after 0.7.1: 005, 010, 023, 030, 035. Lost: 003, which passed in 0.7.1 only because
ICA, fitted on everything, happened to absorb its bad Fp1; with the break and peak-to-peak
stretches left out (ERP CORE's 600 µV for 003 as well as 400 µV) Fp1 fails the peak-to-peak test.

4. **Channels persistently noisy after ICA (0.9.0).** 003's Fp1 and 033's F7/F8 are not
   intermittent: on the cleaned data their 1 s peak-to-peak is 2.1-2.4 x the median channel's
   in almost every minute (a clean subject: 1.0-1.2 x), below pyprep's deviation cutoff, and
   they fail the 100 µV epoch test throughout the recording (165 and 135 rejected epochs). A
   channel flagged when its median 1 s peak-to-peak ratio exceeds a threshold, added to
   `pyprep_robust`, scored against ERP CORE's 27 hand-picked bad channels:

   | criterion | matches ERP CORE | flagged, not ERP CORE's | missed |
   |---|---|---|---|
   | `pyprep_robust` alone | 10 | 4 | 17 |
   | + before ICA, > 2 x | 18 | 65 | 9 |
   | + before ICA, > 3 x | 12 | 32 | 15 |
   | + after ICA, > 1.5 x | 27 | 44 | 0 |
   | + after ICA, > 2 x | 19 | 9 | 8 |
   | + after ICA, > 2.5 x | 11 | 5 | 16 |

   Before ICA, blinks put Fp1/Fp2 at 3-5 x in most subjects, so the check runs after cleaning:
   `artifacts.post_bad_channels: {max_ptp_ratio: 2.0}` (opt-in, on in `erpcore_n170_full.yaml`
   only) interpolates those channels at the end of the ica stage and applies the reference
   again; QA `ICA_POST_BADS`. On the 40 subjects it interpolated 1-3 channels in 11 (003 Fp1,
   033 F7/F8, 022 Fp1/Fp2/F8, ...). The 2 x threshold was chosen on these same 40 subjects, so
   it is not a default until it is checked on another dataset. Regression test:
   `test_erpcore_n170_sub003_persistently_noisy_channel_interpolated`.

   Out-of-sample check on ERP CORE P3 and N2pc, subjects 015-019 (the 5-subject BIDS archive;
   2026-11-02): ERP CORE's bad channels removed from the configs (`pyprep_robust` instead),
   ICA with `fit_exclude`, ratios on the cleaned data. ERP CORE interpolated no channel in any
   of the 40 P3 recordings and 3 in N2pc (015 F7, 017 F8/C6). At 2 x the check flags nothing
   in the 10 recordings (highest ratio 1.85, N2pc 015 F8): no false alarm, and it misses the
   three N2pc channels (ratios 1.29-1.63). At 1.5 x it would add 5 channels in P3 and 3 in
   N2pc (one of them ERP CORE's). `pyprep_robust` flags PO3 in 018 in both tasks; ERP CORE kept
   it. The GABA infant data (VEP section) are the other out-of-sample check.

Subject 029 failed the ica stage in v0.7.0 ("one PCA component captures 99.996 % of the
variance"). Cause: Oz is normal most of the time (median amplitude 0.83 x the other channels)
but has stretches of up to 362 mV in about 8 % of the samples. pyprep RANSAC flags a channel
only when it is bad in more than 40 % of its windows, and pyprep's deviation test is robust
(median/IQR), so neither flagged Oz; the average reference then spread it into every channel.
Its plain variance on the 1 Hz high-passed copy is about 10^6 x the median channel's (next:
Fp2, 11 x, blinks). Fix: `preproc.bad_channels.max_variance_ratio` (default 100) adds any
channel above that ratio to the automatic methods' bads (reason `gross variance`); on the 40
subjects it flags only 029's Oz, which ERP CORE also interpolated. 029 then gave −3.35 µV vs
ERP CORE's −2.93 (−0.41, within tolerance; −3.31 with the recipe after 0.7.1). Regression test:
`test_erpcore_n170_sub029_gross_variance_channel_flagged`.

`erpcore-exact` deviations above 0.5 µV: 004 (+0.52), 008 (+0.92), 029 (−0.59). The parent waves
(face, car) are each about 0.3 µV less positive than ERP CORE's on average (outside the ±0.3 µV
mean tolerance) while the difference wave agrees within 0.06 µV: the same parent-wave offset seen
on sub-015..019, which cancels in the contrast. Both acceptance criteria (mean within ±0.3 µV,
d_z with ERP CORE's sign) pass for both recipes.
