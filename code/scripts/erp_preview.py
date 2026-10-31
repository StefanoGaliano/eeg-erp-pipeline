"""README figure and measures: ERP CORE N170 through the eegunify ERP stages.

A thin wrapper over the library: runs ``eegunify erp run`` (preproc -> ica -> epochs -> erp) on
a converted project, copies the erp stage's own N170 figure to ``docs/img`` and prints the
primary measures as a Markdown table for the README. ``--recipe preview`` runs the minimal
v0.1.0 preview recipe instead (``eegunify.erp.recipes``), whose face - car value at PO8 is the
-2.24 µV regression golden value.

    eegunify fetch erpcore -s 015 --dest sourcedata/erpcore
    eegunify convert code/configs/erpcore_n170.yaml -s 015
    python code/scripts/erp_preview.py --root . --subject 015
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd

from eegunify.config.loader import load_config
from eegunify.erp import paths
from eegunify.erp.recipes import apply_recipe
from eegunify.erp.runner import Runner

CONFIG = Path("code/configs/erpcore_n170.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--subject", default="015")
    parser.add_argument("--recipe", choices=["erpcore", "preview"], default="erpcore")
    parser.add_argument("--out", type=Path, default=Path("docs/img/erpcore_n170_erp.png"))
    args = parser.parse_args()
    cfg = load_config(args.root / CONFIG)
    if args.recipe == "preview":
        cfg = apply_recipe(cfg, "preview")
    for o in Runner(cfg, args.root).run(subjects=[args.subject]):
        print(f"{o.item.stage:8s} {o.status} {o.message}")
        if o.status == "failed":
            raise SystemExit(1)
    folder = paths.subject_dir(args.root, "erp", cfg, args.subject)
    [png] = folder.glob("*_desc-stimN170_erp.png")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(png, args.out)
    [tsv] = folder.glob("*_desc-stim_measures.tsv")
    m = pd.read_csv(tsv, sep="\t")
    m = m[m["roi"] == "primary"][["wave", "channels", "measure", "value", "unit", "n_trials"]]
    print("| " + " | ".join(m.columns) + " |")
    print("|" + "---|" * len(m.columns))
    for row in m.itertuples(index=False):
        cells = [f"{v:.2f}" if isinstance(v, float) else str(v) for v in row]
        print("| " + " | ".join(cells) + " |")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
