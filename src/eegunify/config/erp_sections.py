"""Config sections for the ERP phases (docs/BLUEPRINT_PHASES.md section 5.1).

All of them are optional: a Phase 1 config (conversion only) stays valid and gets these
defaults, which nothing reads until ``eegunify erp`` runs. The models live here to keep
``schema.py`` readable; ``schema.py`` imports them so they appear in ``config_reference.md``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EPOCH_SET_RE = re.compile(r"^[A-Za-z0-9]+$")
ICLABEL_CLASSES = (
    "brain",
    "muscle artifact",
    "eye blink",
    "heart beat",
    "line noise",
    "channel noise",
    "other",
)
"""ICLabel classes in the order of its probability columns, spelled as mne-icalabel does."""
RESERVED_DESC = frozenset({"preproc", "ica", "clean", "epochs", "erp"})
"""``desc-`` values used by the stages themselves; epoch sets cannot take them."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


# --- preproc ---------------------------------------------------------------------------------


class LineNoiseSpec(_Strict):
    """Line-noise removal, the first preprocessing step."""

    method: Literal["none", "notch", "zapline"] = "none"
    """``notch`` (MNE FIR notch), ``zapline`` (meegkit, needs the ``erp`` extra) or ``none``."""
    freqs: list[float] | Literal["auto"] = "auto"
    """Frequencies to remove; ``auto`` takes the line frequency and harmonics found by QA."""


class FilterSpec(_Strict):
    """Filter design shared by the high-pass and low-pass steps."""

    method: Literal["iir", "fir"] = "iir"
    """``iir`` Butterworth (as ERPLAB ``pop_basicfilter``) or ``fir`` (MNE default design)."""
    iir_order: int = Field(default=2, ge=1, le=8)
    """Butterworth order counted as ERPLAB does: the total order of the forward-backward
    filter, so 2 = 12 dB/oct with a half-amplitude (-6 dB) cutoff. Must be even for
    ``phase: zero`` (each pass is order/2)."""
    phase: Literal["zero", "minimum"] = "zero"
    """``zero`` (non-causal, offline ERPs) or ``minimum`` (causal; the seam for real-time use)."""

    @model_validator(mode="after")
    def _even_order(self) -> FilterSpec:
        if self.method == "iir" and self.phase == "zero" and self.iir_order % 2:
            raise ValueError("filter.iir_order must be even with phase 'zero' (forward-backward)")
        return self


class BadChannelsSpec(_Strict):
    """How bad channels are found before interpolation."""

    method: Literal["config_only", "pyprep_ransac", "pyprep_full", "lof"] = "config_only"
    """``config_only`` uses ``channels.bads`` alone; the others add automatic detection on a
    1 Hz high-passed copy (pyprep needs electrode positions)."""
    random_state: int = 97
    """Seed for pyprep RANSAC; part of the cache key."""


class PreprocSection(_Strict):
    """Continuous-data cleaning (Phase 2): line noise, filter, bad channels, interpolation."""

    line_noise: LineNoiseSpec = Field(default_factory=LineNoiseSpec)
    """Line-noise removal."""
    highpass_hz: float | None = Field(default=0.1, gt=0)
    """High-pass cutoff in Hz (ERP CORE: 0.1 Hz); ``null`` skips it."""
    lowpass_hz: float | None = Field(default=None, gt=0)
    """Low-pass cutoff in Hz applied to the data that get measured; ``null`` skips it. Figures
    use their own plotting low-pass."""
    filter: FilterSpec = Field(default_factory=FilterSpec)
    """Filter design."""
    resample_hz: float | None = Field(default=None, gt=0)
    """Resample to this rate before filtering; ``null`` keeps the recorded rate."""
    bad_channels: BadChannelsSpec = Field(default_factory=BadChannelsSpec)
    """Bad-channel detection."""
    interpolate: bool = True
    """Spherical-spline interpolation of bad channels before re-referencing."""

    @model_validator(mode="after")
    def _band(self) -> PreprocSection:
        if self.highpass_hz and self.lowpass_hz and self.lowpass_hz <= self.highpass_hz:
            raise ValueError("preproc.lowpass_hz must be above preproc.highpass_hz")
        return self


# --- artifacts -------------------------------------------------------------------------------


class IcLabelSpec(_Strict):
    """Automatic component exclusion by ICLabel class."""

    exclude_classes: list[str] = Field(default_factory=lambda: ["eye blink"])
    """ICLabel classes to remove, spelled as mne-icalabel spells them (``eye blink``, ...)."""
    threshold: float = Field(default=0.8, ge=0, le=1)
    """Minimum class probability for exclusion."""

    @field_validator("exclude_classes")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in ICLABEL_CLASSES]
        if unknown:
            raise ValueError(f"unknown ICLabel classes {unknown}; use {list(ICLABEL_CLASSES)}")
        return v


class EogCorrelationSpec(_Strict):
    """Second vote: components correlated with EOG channels."""

    channels: list[str] = Field(default_factory=lambda: ["VEOG", "HEOG"])
    """EOG channels to correlate with (missing ones are skipped)."""
    threshold: float = Field(default=3.0, gt=0)
    """z-score above which a component counts as ocular."""


class IcaSpec(_Strict):
    """ICA fit, classification and manual choices."""

    fit_highpass_hz: float = Field(default=1.0, gt=0)
    """High-pass of the copy ICA is fitted on (the solution is applied to the original)."""
    n_components: float | int = 0.99
    """Number of components, or the fraction of variance to keep when below 1."""
    algorithm: Literal["picard", "infomax", "fastica"] = "picard"
    """ICA algorithm."""
    extended: bool = True
    """Extended (sub- and super-Gaussian) variant for picard/infomax."""
    random_state: int = 97
    """Seed; part of the cache key."""
    iclabel: IcLabelSpec = Field(default_factory=IcLabelSpec)
    """ICLabel exclusion rule."""
    eog_correlation: EogCorrelationSpec = Field(default_factory=EogCorrelationSpec)
    """EOG-correlation rule."""
    exclude: list[int] = Field(default_factory=list)
    """Component indices to remove by hand (usually per subject, in ``overrides``)."""
    include: list[int] = Field(default_factory=list)
    """Component indices to keep whatever the automatic rules say."""


class AsrSpec(_Strict):
    """Artifact subspace reconstruction (meegkit), run before ICA when enabled."""

    cutoff: float = Field(default=20.0, gt=0)
    """Standard-deviation cutoff for burst rejection."""


class ArtifactsSection(_Strict):
    """Artifact correction (Phase 3)."""

    method: Literal["ica", "none"] = "ica"
    """``ica`` runs the ICA stage; ``none`` epochs the preprocessed data directly."""
    ica: IcaSpec = Field(default_factory=IcaSpec)
    """ICA settings."""
    asr: AsrSpec | None = None
    """ASR settings; ``null`` (default) keeps ASR off."""
    eog_bipolar: dict[str, tuple[str, str]] = Field(default_factory=dict)
    """Bipolar EOG derivations, ``{VEOG: [FP1, VEOG_lower]}`` = first minus second."""


# --- epochs ----------------------------------------------------------------------------------


class EpochSet(_Strict):
    """One set of epochs, written as ``desc-<name>``."""

    lock: str
    """Trial-type prefix selector the epochs are time-locked to (``stimulus/*``,
    ``response/*``); matched with MNE tags, never exact labels."""
    tmin_ms: float = -200.0
    """Epoch start relative to the event."""
    tmax_ms: float = 800.0
    """Epoch end relative to the event."""
    baseline_ms: tuple[float, float] | None = (-200.0, 0.0)
    """Baseline window; ``null`` for no baseline correction."""

    @model_validator(mode="after")
    def _window(self) -> EpochSet:
        if self.tmax_ms <= self.tmin_ms:
            raise ValueError("tmax_ms must be greater than tmin_ms")
        if self.baseline_ms is not None:
            lo, hi = self.baseline_ms
            if not self.tmin_ms <= lo < hi <= self.tmax_ms:
                raise ValueError("baseline_ms must be an increasing window inside the epoch")
        return self


class RejectSpec(_Strict):
    """Epoch rejection."""

    method: Literal["thresholds", "autoreject", "none"] = "thresholds"
    """``thresholds`` (ERPLAB semantics), ``autoreject`` (local) or ``none``."""
    ptp_uv: float | None = Field(default=100.0, gt=0)
    """Moving-window peak-to-peak threshold on EEG (ERPLAB ``pop_artmwppth``)."""
    ptp_window_ms: float = Field(default=200.0, gt=0)
    """Moving-window width."""
    ptp_step_ms: float = Field(default=100.0, gt=0)
    """Moving-window step."""
    step_heog_uv: float | None = Field(default=32.0, gt=0)
    """Step-function threshold on HEOG (ERPLAB ``pop_artstep``); only if HEOG exists."""
    step_window_ms: float = Field(default=200.0, gt=0)
    """Step-function window: second-half mean minus first-half mean within it."""
    step_step_ms: float = Field(default=50.0, gt=0)
    """Step-function window step."""
    flat_uv: float | None = Field(default=None, gt=0)
    """Reject epochs whose peak-to-peak on any EEG channel is below this."""
    min_trials_per_condition: int = Field(default=30, ge=1)
    """Fewer kept trials fails ``EPOCHS_MIN_TRIALS`` and excludes the subject from group."""
    random_state: int = 97
    """Seed for autoreject; part of the cache key."""


class EpochsSection(_Strict):
    """Epoching and rejection (Phase 4)."""

    sets: dict[str, EpochSet] = Field(default_factory=dict)
    """Epoch sets by name (letters and digits; the name becomes the ``desc-`` entity)."""
    reject: RejectSpec = Field(default_factory=RejectSpec)
    """Rejection rule shared by all sets."""

    @field_validator("sets")
    @classmethod
    def _names(cls, v: dict[str, EpochSet]) -> dict[str, EpochSet]:
        for name in v:
            if not _EPOCH_SET_RE.match(name):
                raise ValueError(f"epoch set name {name!r} must be letters and digits")
            if name in RESERVED_DESC:
                raise ValueError(f"epoch set name {name!r} is reserved")
        return v


# --- erp (averaging and measurement) ----------------------------------------------------------


class ErpSection(_Strict):
    """Averaging, measurement and figures (Phase 5)."""

    peak_neighborhood: int = Field(default=3, ge=1)
    """Samples on each side a local peak must exceed on average (ERPLAB "neighborhood")."""
    plot_lowpass_hz: float | None = Field(default=20.0, gt=0)
    """Low-pass applied to figures only, never to the measured waveforms; ``null`` plots the
    data as measured."""
    figures: bool = True
    """Write one PNG per measured component."""


# --- conditions, contrasts, components, group ------------------------------------------------


class Contrast(_Strict):
    """A difference wave between two named conditions."""

    plus: str
    """Condition with weight +1."""
    minus: str
    """Condition with weight -1."""


class ComponentsSection(_Strict):
    """Which ERP components to measure, from the component library."""

    preset: str = "erpcore"
    """Preset family tried first (``erpcore``, ``classic``, ``iscev``)."""
    use: list[str] = Field(default_factory=list)
    """Component ids (``n170``) or explicit ``id@preset`` keys."""
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Partial component definitions deep-merged over the resolved preset, by component id."""
    library_paths: list[str] = Field(default_factory=list)
    """Extra component YAML files (relative to the project root), e.g.
    ``code/configs/components/mine.yaml``."""


class ClusterSpec(_Strict):
    """Cluster-based permutation test over time x channels."""

    enabled: bool = False
    """Run the test."""
    contrast: str | None = None
    """Contrast (difference wave) to test against zero."""
    tmin_ms: float = 0.0
    """Start of the tested window."""
    tmax_ms: float = 400.0
    """End of the tested window."""
    n_permutations: int = Field(default=5000, ge=1)
    """Number of sign-flip permutations."""
    threshold: float | None = None
    """Cluster-forming threshold (t); ``null`` uses MNE's default."""
    tail: Literal[-1, 0, 1] = 0
    """-1 negative, 0 two-tailed, 1 positive."""
    random_state: int = 97
    """Seed; part of the cache key."""


GroupTest = Literal["paired_t", "wilcoxon"]
_DEFAULT_TESTS: tuple[GroupTest, ...] = ("paired_t", "wilcoxon")


class GroupSection(_Strict):
    """Group statistics (Phase 6)."""

    tests: list[GroupTest] = Field(default_factory=lambda: list(_DEFAULT_TESTS))
    """Tests run per component and measure."""
    cluster: ClusterSpec = Field(default_factory=ClusterSpec)
    """Cluster permutation settings."""
    reference_values: Literal["erpcore", "none"] = "none"
    """Compare measures with published values (``erpcore``) or not."""
