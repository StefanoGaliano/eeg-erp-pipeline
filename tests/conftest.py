"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.synthetic.generate import WRITERS, Truth, biosemi64_truth, make_truth


@pytest.fixture(scope="session")
def truth() -> Truth:
    return make_truth()


@pytest.fixture(scope="session")
def truth64() -> Truth:
    return biosemi64_truth()


@pytest.fixture(scope="session")
def synthetic_files(
    tmp_path_factory: pytest.TempPathFactory, truth: Truth, truth64: Truth
) -> dict[str, Path]:
    """One synthetic recording per format, written once per test session."""
    root = tmp_path_factory.mktemp("synthetic")
    out = {}
    for fmt, writer in WRITERS.items():
        out[fmt] = writer(root / f"sub-01_{fmt}", truth64 if fmt == "bdf" else truth)
    return out


@pytest.fixture
def make_project(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Build a mini project (sourcedata + config) around one synthetic file."""

    def _make(src: Path, fmt: str, extra: dict | None = None) -> tuple[Path, Path]:
        source_dir = tmp_path / "sourcedata" / "synth"
        source_dir.mkdir(parents=True, exist_ok=True)
        for f in src.parent.glob(src.stem + ".*"):
            (source_dir / f.name.replace(src.stem, "sub-01_task-faces")).write_bytes(f.read_bytes())
        # BrainVision headers reference their companions by name: rewrite them.
        vhdr = source_dir / "sub-01_task-faces.vhdr"
        if vhdr.exists():
            vhdr.write_text(vhdr.read_text().replace(src.stem, "sub-01_task-faces"))
            vmrk = source_dir / "sub-01_task-faces.vmrk"
            vmrk.write_text(vmrk.read_text().replace(src.stem, "sub-01_task-faces"))
        cfg = {
            "dataset": {
                "name": "Synthetic",
                "source_root": "sourcedata/synth",
                "task": "faces",
                "authors": ["Test"],
                "license": "CC0",
            },
            "recordings": {"pattern": "sub-{subject}_task-faces" + src.suffix, "subjects": ["01"]},
            "reference": {"online": "Cz"},
            "events": {"mapping": {"1": "stimulus/face", "2": "stimulus/car"}, "unmapped": "error"},
        }
        for key, val in (extra or {}).items():
            cfg.setdefault(key, {}).update(val)
        path = tmp_path / "code" / "configs" / "synth.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(cfg))
        return tmp_path, path

    return _make


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden", action="store_true", help="Rewrite tests/golden/ from current output."
    )


@pytest.fixture(scope="session")
def data_cache() -> Path:
    """Where real-data tests keep downloads (``EEGUNIFY_DATA`` or ``~/.cache/eegunify``)."""
    import os

    root = Path(os.environ.get("EEGUNIFY_DATA", Path.home() / ".cache" / "eegunify"))
    root.mkdir(parents=True, exist_ok=True)
    return root
