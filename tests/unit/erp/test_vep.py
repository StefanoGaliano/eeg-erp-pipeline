"""VEP presets and the component-level reference (docs/BLUEPRINT_PHASES.md section 12).

Acceptance: on the synthetic pattern-reversal VEP the ``vep_p100@iscev`` peak latency is
recovered within ±2 ms for both check sizes. The data are average-referenced and the ISCEV
presets name Fz, so the components are measured on Fz-referenced copies: the amplitudes match
the noise-free Oz - Fz wave, not the average-referenced one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import mne
import numpy as np
import pandas as pd
import pytest

from eegunify.config.loader import parse_config
from eegunify.erp import paths, references
from eegunify.erp.references import ComponentReferenceError
from eegunify.erp.runner import Runner
from tests.synthetic.erp import ErpTruth, vep_config

LATENCY_TOL_MS = 2.0  # P100, the blueprint's acceptance (one sample at 500 Hz)
#: N75 and N135 are broader troughs; over 8 seeds x 2 check sizes the worst errors were 2 ms
#: (N75) and 4 ms (N135), P100 2 ms and amplitude 0.86 µV.
SIDE_PEAK_TOL_MS = 4.0
AMPLITUDE_TOL_UV = 1.0


@pytest.fixture(scope="module")
def vep_run(vep_rawdata: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object]:
    root = tmp_path_factory.mktemp("vep_run") / "project"
    shutil.copytree(vep_rawdata, root, symlinks=True)
    cfg = parse_config(vep_config())
    outs = {o.item.stage: o for o in Runner(cfg, root).run()}
    assert outs["erp"].status == "ok", outs["erp"].message
    return root, cfg


def _out(root: Path, cfg: object, desc: str, suffix: str, ext: str) -> Path:
    return paths.stage_path(root, "erp", cfg, "01", description=desc, suffix=suffix, extension=ext)  # type: ignore[arg-type]


def _measures(vep_run: tuple[Path, object]) -> pd.DataFrame:
    root, cfg = vep_run
    m = pd.read_csv(_out(root, cfg, "stim", "measures", ".tsv"), sep="\t")
    return m[m["roi"] == "primary"]


@pytest.mark.parametrize("cond", ["large", "small"])
def test_vep_latencies_recovered(
    vep_run: tuple[Path, object], vep_truth: ErpTruth, cond: str
) -> None:
    m = _measures(vep_run)
    lat = m[(m["wave"] == cond) & (m["measure"] == "peak_latency")].set_index("component")
    truth = vep_truth.vep[cond]
    assert lat.loc["vep_p100", "value"] == pytest.approx(truth["p100_ms"], abs=LATENCY_TOL_MS)
    for comp, key in (("vep_n75", "n75_ms"), ("vep_n135", "n135_ms")):
        assert lat.loc[comp, "value"] == pytest.approx(truth[key], abs=SIDE_PEAK_TOL_MS), comp


def test_vep_amplitude_is_oz_minus_fz(vep_run: tuple[Path, object], vep_truth: ErpTruth) -> None:
    """Measured on the Fz-referenced copy; the written averages keep the average reference."""
    root, cfg = vep_run
    m = _measures(vep_run)
    amp = m[(m["component"] == "vep_p100") & (m["measure"] == "peak_amplitude")]
    amp = amp.set_index("wave")["value"]
    evokeds = {e.comment: e for e in mne.read_evokeds(_out(root, cfg, "stim", "ave", ".fif"))}
    for cond in ("large", "small"):
        truth = vep_truth.vep[cond]
        assert amp[cond] == pytest.approx(truth["p100_uv"], abs=AMPLITUDE_TOL_UV)
        ev = evokeds[cond]
        sel = (ev.times >= 0.09) & (ev.times <= 0.13)
        avg_ref_peak = float(ev.copy().pick(["Oz"]).data[0, sel].max() * 1e6)
        assert abs(avg_ref_peak - amp[cond]) > 2 * AMPLITUDE_TOL_UV  # the reference mattered


def test_vep_reference_and_latency_qa(vep_run: tuple[Path, object], vep_truth: ErpTruth) -> None:
    root, cfg = vep_run
    qa = json.loads(_out(root, cfg, "erp", "qa", ".json").read_text(encoding="utf-8"))
    refs = qa["component_references"]
    assert set(refs) == {"vep_n75", "vep_p100", "vep_n135"}
    assert refs["vep_p100"] == {"data": "average", "component": "Fz"}
    checks = {c["id"]: c for c in qa["checks"]}
    assert "vep_p100: average -> Fz" in checks["ERP_REFERENCE_CHANGED"]["message"]
    latency = checks["VEP_LATENCY_ATYPICAL"]["value"]  # first condition: large checks
    assert latency == pytest.approx(vep_truth.vep["large"]["p100_ms"], abs=LATENCY_TOL_MS)
    assert "atypically late" not in checks["VEP_LATENCY_ATYPICAL"]["message"]


# --- reference rules (no data) ----------------------------------------------------------------


def _cfg(**reference: object) -> object:
    return parse_config(vep_config(reference=reference))


def test_parse_and_needed() -> None:
    assert references.parse("P9/P10").channels == ("P9", "P10")
    assert references.parse("P9 + P10").key() == references.parse("p10/p9").key()
    assert references.parse("Average").kind == "average"
    avg = _cfg(scheme="average")
    assert references.needed(None, avg) is None  # type: ignore[arg-type]
    assert references.needed("average", avg) is None  # type: ignore[arg-type]
    assert references.needed("Fz", avg).label == "Fz"  # type: ignore[arg-type, union-attr]
    mastoids = _cfg(scheme="channels", channels=["P10", "P9"])
    assert references.needed("P9/P10", mastoids) is None  # type: ignore[arg-type]


def _evoked(names: list[str], bads: tuple[str, ...] = ()) -> mne.EvokedArray:
    info = mne.create_info(names, 500.0, "eeg")
    info["bads"] = list(bads)
    data = np.arange(len(names), dtype=float)[:, None] * np.ones((1, 10)) * 1e-6
    return mne.EvokedArray(data, info, tmin=0.0, verbose=False)


def test_apply_channels_average_and_errors() -> None:
    cfg = _cfg(scheme="average")
    ev = _evoked(["Oz", "Fz", "Cz"])
    fz = references.apply(ev, references.parse("Fz"), cfg)  # type: ignore[arg-type]
    np.testing.assert_allclose(fz.data[:, 0] * 1e6, [-1.0, 0.0, 1.0])
    assert ev.data[0, 0] == 0.0  # a copy: the input is untouched
    avg = references.apply(ev, references.parse("average"), cfg)  # type: ignore[arg-type]
    np.testing.assert_allclose(avg.data[:, 0] * 1e6, [-1.0, 0.0, 1.0])
    with pytest.raises(ComponentReferenceError, match="not in the data"):
        references.apply(ev, references.parse("P9/P10"), cfg)  # type: ignore[arg-type]
    with pytest.raises(ComponentReferenceError, match="bad"):
        references.apply(_evoked(["Oz", "Fz"], ("Fz",)), references.parse("Fz"), cfg)  # type: ignore[arg-type]


def test_apply_online_reference_absent_from_data() -> None:
    """Recorded against Fz, never re-referenced: Fz is not a channel, and nothing to do."""
    cfg = _cfg(online="Fz", scheme="none")
    assert references.needed("Fz", cfg) is None  # type: ignore[arg-type]
    ev = _evoked(["Oz", "Cz"])
    same = references.apply(ev, references.parse("fz"), cfg)  # type: ignore[arg-type]
    np.testing.assert_array_equal(same.data, ev.data)
