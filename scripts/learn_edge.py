#!/usr/bin/env python3
"""Learn model calibration / edge over time from completed WC results.

Builds (predicted_probability, outcome) pairs from every completed match - one per
home/draw/away outcome - fits a calibration curve (Platt scaling, shrunk to identity on
small samples), and records how calibration improves as results accrue.

Outputs:
  outputs/backtesting/edge_calibration.json        - the current calibration (used by the
                                                     model-probabilities converter)
  outputs/backtesting/edge_learning_history.csv    - one row per run: n, Brier before/after
  outputs/backtesting/edge_reliability.csv         - reliability bins (predicted vs observed)
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superbru_score_engine.betting.edge_learning import (  # noqa: E402
    brier,
    fit_calibration,
    log_loss,
    reliability_table,
)

RESULTS = "outputs/superbru_pool/superbru_match_results_auto.csv"
PER_MATCH = "outputs/backtesting/wc_predictive_power/wc_predictive_power_per_match.csv"
PLOG = "outputs/backtesting/prediction_log.csv"
OUT_DIR = "outputs/backtesting"
OUTCOMES = ("home", "draw", "away")
ALIASES = {"czechia": "czech republic", "ir iran": "iran", "bosnia and herzegovina": "bosnia herzegovina",
           "congo dr": "dr congo", "cote d ivoire": "ivory coast", "cabo verde": "cape verde",
           "turkiye": "turkey", "korea republic": "south korea", "usa": "united states"}


def tk(n):
    t = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return ALIASES.get(t, t)


def mk(h, a):
    return f"{tk(h)}__{tk(a)}"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_pairs():
    """Return (probs, outcomes, fav_probs, fav_outcomes) from all completed matches, deduped by match."""
    probs, outs = [], []
    fav_probs, fav_outs = [], []
    seen = set()

    def add_match(p3, actual):
        if None in p3 or actual not in OUTCOMES:
            return
        for i, o in enumerate(OUTCOMES):
            probs.append(p3[i])
            outs.append(1.0 if o == actual else 0.0)
        fav_i = max(range(3), key=lambda i: p3[i])
        fav_probs.append(p3[fav_i])
        fav_outs.append(1.0 if OUTCOMES[fav_i] == actual else 0.0)

    if Path(PER_MATCH).exists():
        for r in csv.DictReader(open(PER_MATCH, encoding="utf-8-sig")):
            key = mk(r.get("home_team", ""), r.get("away_team", ""))
            if key in seen or not r.get("actual_outcome"):
                continue
            seen.add(key)
            add_match([_f(r.get("p_home")), _f(r.get("p_draw")), _f(r.get("p_away"))], r["actual_outcome"])

    if Path(RESULTS).exists() and Path(PLOG).exists():
        res = {}
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            if str(r.get("is_completed")).lower() == "true" and r["home_goals"] and r["away_goals"]:
                hg, ag = int(r["home_goals"]), int(r["away_goals"])
                res[mk(r["home_team"], r["away_team"])] = "home" if hg > ag else ("away" if hg < ag else "draw")
        latest = {}
        for r in csv.DictReader(open(PLOG, encoding="utf-8-sig")):
            k = r["match_key"]
            if k not in latest or r.get("snapshot_utc", "") > latest[k].get("snapshot_utc", ""):
                latest[k] = r
        for k, r in latest.items():
            if k in seen or k not in res:
                continue
            seen.add(k)
            add_match([_f(r.get("p_home")), _f(r.get("p_draw")), _f(r.get("p_away"))], res[k])

    return probs, outs, fav_probs, fav_outs


def main() -> int:
    ap = argparse.ArgumentParser(description="Learn model calibration / edge from completed results")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--l2", type=float, default=1.0, help="Shrinkage toward identity (higher = safer/less change)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    probs, outs, fav_probs, fav_outs = build_pairs()
    cal = fit_calibration(probs, outs, l2=args.l2, fitted_at_utc=now)

    (out_dir / "edge_calibration.json").write_text(cal.to_json(), encoding="utf-8")

    rel = reliability_table(probs, outs)
    with (out_dir / "edge_reliability.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["band", "n", "mean_predicted", "observed_freq", "gap_observed_minus_predicted"])
        w.writeheader()
        w.writerows(rel)

    hist_path = out_dir / "edge_learning_history.csv"
    hist_fields = ["fitted_at_utc", "matches", "outcome_samples", "a", "b",
                   "brier_before", "brier_after", "log_loss_before", "log_loss_after",
                   "favourite_predicted", "favourite_observed"]
    new = not hist_path.exists()
    with hist_path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=hist_fields)
        if new:
            w.writeheader()
        w.writerow({
            "fitted_at_utc": now, "matches": len(fav_probs), "outcome_samples": len(probs),
            "a": round(cal.a, 4), "b": round(cal.b, 4),
            "brier_before": _r(cal.brier_before), "brier_after": _r(cal.brier_after),
            "log_loss_before": _r(cal.log_loss_before), "log_loss_after": _r(cal.log_loss_after),
            "favourite_predicted": _r(brier(fav_probs, fav_outs) is not None and sum(fav_probs) / len(fav_probs) if fav_probs else None),
            "favourite_observed": _r(sum(fav_outs) / len(fav_outs) if fav_outs else None),
        })

    print("=" * 72)
    print("EDGE LEARNING - model calibration from completed results")
    print("=" * 72)
    print(f"matches: {len(fav_probs)}   outcome samples: {len(probs)}")
    if not probs:
        print("No completed results with probabilities yet - calibration stays identity.")
        return 0
    print(f"calibration: a={cal.a:.3f} b={cal.b:.3f} (identity = a1 b0)  [n>={8} needed to move off identity]")
    print(f"Brier   before {cal.brier_before:.4f} -> after {cal.brier_after:.4f} "
          f"({(cal.brier_after - cal.brier_before):+.4f})")
    print(f"log-loss before {cal.log_loss_before:.4f} -> after {cal.log_loss_after:.4f}")
    if fav_probs:
        print(f"favourites: predicted {sum(fav_probs)/len(fav_probs):.1%} vs observed {sum(fav_outs)/len(fav_outs):.1%}")
    print("\nreliability (predicted vs observed):")
    for row in rel:
        print(f"  {row['band']}  n={row['n']:<3} pred {row['mean_predicted']:.3f}  obs {row['observed_freq']:.3f}  "
              f"gap {row['gap_observed_minus_predicted']:+.3f}")
    print(f"\ncalibration -> {out_dir / 'edge_calibration.json'}  (history appended)")
    print("Apply it in the converter: build_polymarket_model_probabilities.py --calibration "
          f"{out_dir / 'edge_calibration.json'}")
    return 0


def _r(v):
    return "" if v is None else round(float(v), 4)


if __name__ == "__main__":
    raise SystemExit(main())
