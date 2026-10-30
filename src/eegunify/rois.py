"""Visual regions of interest (blueprint section 7.3), used by QA and later ERP phases."""

from __future__ import annotations

VISUAL_ROIS: dict[str, tuple[str, ...]] = {
    "occipital": ("O1", "Oz", "O2"),
    "parieto_occipital": ("PO7", "PO3", "POz", "PO4", "PO8"),
    "lateral_occipitotemporal_left": ("P7", "PO7"),  # N170 left
    "lateral_occipitotemporal_right": ("P8", "PO8"),  # N170 right
    "centro_parietal": ("Cz", "CPz", "Pz"),  # P300
}

#: ROIs whose channels QA expects to be present and good in a visual study.
CRITICAL_VISUAL_ROIS = (
    "occipital",
    "lateral_occipitotemporal_left",
    "lateral_occipitotemporal_right",
)


def missing_visual_channels(ch_names: list[str], bads: list[str]) -> dict[str, list[str]]:
    """For each critical visual ROI, the channels that are absent or marked bad."""
    present = set(ch_names) - set(bads)
    out = {}
    for roi in CRITICAL_VISUAL_ROIS:
        miss = [c for c in VISUAL_ROIS[roi] if c not in present]
        if miss:
            out[roi] = miss
    return out
