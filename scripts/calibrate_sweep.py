#!/usr/bin/env python3
"""Grid-sweep devig_method x dixon_coles_rho x ratings_weight for the Superbru
score engine.

For each combination it writes a throwaway config (the base config with just
those three model fields overridden), runs the Football-Data league backtest,
and saves that run's full output so you can pick the config that maximises
Superbru expected points against the naive baseline.

Run it WITH YOUR PYTHON (the one that can already import the engine), e.g.:

    & $python calibrate_sweep.py --base-config config.yaml --out-root outputs\\calibration\\sweep

Quick plumbing check first (6 combos, reuse cached CSVs after one download):
    & $python calibrate_sweep.py --devig power,multiplicative --rho -0.04,-0.06,-0.08 --ratings 0.0

Notes
-----
* The backtest reads model params from the YAML config, so this just rewrites
  config.model.{devig_method,dixon_coles_rho,ratings_weight,odds_weight} per run.
* Output parsing is best-effort - the backtest's exact summary format is not
  assumed. Each run's stdout/stderr is saved to <out-root>/<combo>/run.log and
  the backtest's own files land in <out-root>/<combo>/backtest/. Paste one
  run.log back and exact auto-ranking can be wired in.
* If `... football-data-league-backtest --help` shows a built-in rho sweep,
  prefer that for rho; this harness still adds the devig x ratings_weight grid.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import pathlib
import re
import subprocess
import sys

import yaml

DEVIG_METHODS = ["power", "multiplicative", "additive"]
RHO_GRID = [-0.02, -0.04, -0.06, -0.08, -0.10]
RATINGS_WEIGHTS = [0.0, 0.05, 0.10]

# Best-effort: surface lines that look like a score/quality metric.
METRIC_RE = re.compile(r"(points|\bev\b|baseline|mean|edge|brier|log[_ ]?loss|accuracy)", re.I)


def build_config(base: dict, devig: str, rho: float, ratings_w: float) -> dict:
    cfg = copy.deepcopy(base)
    model = cfg.setdefault("model", {})
    model["devig_method"] = devig
    model["dixon_coles_rho"] = rho
    model["ratings_weight"] = ratings_w
    model["odds_weight"] = round(1.0 - ratings_w, 4)
    return cfg


def run_backtest(config_path, out_dir, seasons, divisions, download):
    cmd = [
        sys.executable, "-m", "superbru_score_engine",
        "football-data-league-backtest",
        "--config", str(config_path),
        "--seasons", seasons,
        "--divisions", divisions,
        "--out-dir", str(out_dir),
    ]
    if download:
        cmd.append("--download")
    return cmd, subprocess.run(cmd, capture_output=True, text=True)


def extract_metric(text: str) -> str:
    for line in text.splitlines():
        if METRIC_RE.search(line):
            return line.strip()
    return "(no metric line matched - open run.log)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-config", default="config.yaml")
    ap.add_argument("--out-root", default="outputs/calibration/sweep")
    ap.add_argument("--seasons", default="2122,2223,2324,2425")
    ap.add_argument("--divisions", default="E0,D1,SP1,I1,F1")
    ap.add_argument("--no-download", action="store_true",
                    help="reuse already-downloaded Football-Data CSVs (faster reruns)")
    ap.add_argument("--devig", default=",".join(DEVIG_METHODS),
                    help="comma list, subset of: power,multiplicative,additive")
    ap.add_argument("--rho", default=",".join(str(r) for r in RHO_GRID),
                    help="comma list of dixon_coles_rho values")
    ap.add_argument("--ratings", default=",".join(str(w) for w in RATINGS_WEIGHTS),
                    help="comma list of ratings_weight values (odds_weight = 1 - this)")
    args = ap.parse_args()

    base_path = pathlib.Path(args.base_config)
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    out_root = pathlib.Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    devigs = [d.strip() for d in args.devig.split(",") if d.strip()]
    rhos = [float(r) for r in args.rho.split(",") if r.strip()]
    ratings = [float(w) for w in args.ratings.split(",") if w.strip()]
    combos = list(itertools.product(devigs, rhos, ratings))

    print(f"Sweeping {len(combos)} combos "
          f"({len(devigs)} devig x {len(rhos)} rho x {len(ratings)} ratings_weight)\n")

    rows = []
    for i, (devig, rho, rw) in enumerate(combos, 1):
        tag = f"{devig}_rho{rho}_rw{rw}".replace("-", "m").replace(".", "p")
        combo_dir = out_root / tag
        combo_dir.mkdir(parents=True, exist_ok=True)

        cfg_path = combo_dir / "config.yaml"
        cfg_path.write_text(yaml.safe_dump(build_config(base, devig, rho, rw), sort_keys=False),
                            encoding="utf-8")

        print(f"[{i}/{len(combos)}] devig={devig} rho={rho} ratings_weight={rw} ...")
        cmd, proc = run_backtest(cfg_path, combo_dir / "backtest",
                                 args.seasons, args.divisions, download=not args.no_download)
        (combo_dir / "run.log").write_text(
            "CMD: " + " ".join(cmd) + "\n\n=== STDOUT ===\n" + proc.stdout
            + "\n=== STDERR ===\n" + proc.stderr, encoding="utf-8")

        if proc.returncode != 0:
            head = f"ERROR exit {proc.returncode}"
        else:
            head = extract_metric(proc.stdout)
        print(f"    {head}")
        rows.append((devig, rho, rw, head))

    print("\n=== SWEEP SUMMARY (best-effort metric line per combo) ===")
    for devig, rho, rw, head in rows:
        print(f"devig={devig:<14} rho={rho:<6} rw={rw:<5} | {head}")
    print(f"\nPer-combo logs + backtest outputs: {out_root}")
    print("Pick the combo with the highest mean Superbru points/match that beats "
          "the baseline consistently across seasons (a single best number that "
          "does not hold across folds is overfit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
