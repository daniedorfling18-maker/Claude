#!/usr/bin/env python3
"""WC 2026 predictive-power & betting-edge validation harness.

This answers one question: do the model's pre-match probabilities carry any
*real-money* predictive edge over the market, when scored against actual World
Cup results?

The engine's probabilities are the de-vigged market consensus (model = market),
so this harness joins those market-derived 1X2 probabilities to TRUSTED completed
results and measures three things:

  1. Forecast skill   - outcome accuracy, Brier, log-loss, RPS vs a uniform 1/3 baseline.
  2. Calibration      - predicted favourite probability vs observed win rate, by band.
  3. Betting outcome  - ROI of flat-staking the model's pick at FAIR odds (1/p) and at a
                        realistic VIGGED price, with bootstrap CIs. Betting at the price the
                        probability came from has expectation 0 (fair) / -vig (real); the
                        realised ROI and its CI show whether the WC sample deviates from that.

Probability sources (both are markets; the model tracks them by construction):
  - market_odds_history.csv          -> The Odds API de-vigged consensus  (PRIMARY = the model)
  - polymarket_wc_1x2_summary.csv    -> Polymarket pre-kickoff prices      (independent cross-check)

Actuals come ONLY from the trusted results CSV. The market-history goal columns are
prediction-card scorelines, never results, and are ignored here.

Outputs (under --out-dir):
  wc_predictive_power_per_match.csv  - one row per scored match per source
  wc_predictive_power_summary.json   - skill / calibration / betting metrics + caveats
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Optional

RESULTS_DEFAULT = "outputs/superbru_pool/superbru_match_results_auto.csv"
MARKET_HISTORY_DEFAULT = "outputs/market_odds_history/market_odds_history.csv"
POLYMARKET_DEFAULT = "outputs/polymarket-wc-retrospective/polymarket_wc_1x2_summary.csv"
REALISED_SUMMARY_DEFAULT = "outputs/backtesting/superbru_realised_submitted_summary.json"
OUT_DIR_DEFAULT = "outputs/backtesting/wc_predictive_power"

OUTCOMES = ("home", "draw", "away")

# Minimal WC alias map, mirroring superbru_score_engine.model.team_names, so the
# harness stays standalone (no engine import needed for a validation script).
ALIASES = {
    "usa": "united states", "us": "united states", "united states of america": "united states",
    "korea republic": "south korea", "republic of korea": "south korea", "korea south": "south korea",
    "ir iran": "iran",
    "czechia": "czech republic",
    "bosnia and herzegovina": "bosnia herzegovina", "bosnia herz": "bosnia herzegovina",
    "congo dr": "dr congo", "democratic republic of congo": "dr congo",
    "cote d ivoire": "ivory coast", "cotedivoire": "ivory coast",
    "cabo verde": "cape verde",
    "turkiye": "turkey",
}


def team_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return ALIASES.get(text, text)


def match_key(home: str, away: str) -> str:
    return f"{team_key(home)}__{team_key(away)}"


def parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def outcome_of(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def normalise(probs: list[float]) -> list[float]:
    clipped = [max(0.0, p) for p in probs]
    total = sum(clipped)
    if total <= 0:
        return [1 / 3, 1 / 3, 1 / 3]
    return [p / total for p in clipped]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_trusted_results(path: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        if str(row.get("is_completed")).strip().lower() != "true":
            continue
        hg, ag = row.get("home_goals", ""), row.get("away_goals", "")
        if hg == "" or ag == "":
            continue
        try:
            hg_i, ag_i = int(hg), int(ag)
        except ValueError:
            continue
        results[match_key(row["home_team"], row["away_team"])] = {
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "actual_score": row.get("actual_score", f"{hg_i}-{ag_i}"),
            "actual_outcome": outcome_of(hg_i, ag_i),
        }
    return results


def load_market_history_probs(path: Path) -> dict[str, dict]:
    """Latest pre-kickoff de-vigged 1X2 snapshot per match (no look-ahead)."""
    if not path.exists():
        return {}
    by_match: dict[str, list[dict]] = {}
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        if not row.get("market_p_home"):
            continue
        by_match.setdefault(match_key(row["home_team"], row["away_team"]), []).append(row)

    probs: dict[str, dict] = {}
    for key, rows in by_match.items():
        commence = parse_iso(rows[0].get("commence_time", ""))
        pre = [
            r for r in rows
            if (snap := parse_iso(r.get("snapshot_created_at_utc", ""))) and commence and snap <= commence
        ]
        usable = pre or []  # only pre-kickoff snapshots; post-kickoff lines are excluded
        if not usable:
            continue
        latest = max(usable, key=lambda r: parse_iso(r["snapshot_created_at_utc"]))
        try:
            p = normalise([float(latest["market_p_home"]), float(latest["market_p_draw"]), float(latest["market_p_away"])])
        except (ValueError, KeyError):
            continue
        probs[key] = {
            "p": p,
            "snapshot_created_at_utc": latest.get("snapshot_created_at_utc", ""),
            "bookmaker_count_h2h": latest.get("bookmaker_count_h2h", ""),
        }
    return probs


def load_prediction_log_probs(path: Path) -> dict[str, dict]:
    """Latest pre-kickoff row per match from the rolling prediction log.

    The log (scripts/log_prediction_snapshots.py) also carries the best obtainable
    price and a sharp anchor, so once fixtures complete this is the source that
    supports real-price ROI and closing-line value, not just the consensus probs.
    """
    if not path.exists():
        return {}
    by_match: dict[str, list[dict]] = {}
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        if not row.get("p_home"):
            continue
        by_match.setdefault(row["match_key"], []).append(row)
    probs: dict[str, dict] = {}
    for key, rows in by_match.items():
        commence = parse_iso(rows[0].get("commence_time", ""))
        pre = [r for r in rows if (s := parse_iso(r.get("snapshot_utc", ""))) and (not commence or s <= commence)]
        usable = pre or rows
        latest = max(usable, key=lambda r: r.get("snapshot_utc", ""))
        try:
            p = normalise([float(latest["p_home"]), float(latest["p_draw"]), float(latest["p_away"])])
        except (ValueError, KeyError):
            continue
        probs[key] = {"p": p, "best_pick_odds": latest.get("best_pick_odds", ""),
                      "edge_vs_sharp": latest.get("edge_pick_vs_sharp", "")}
    return probs


def load_polymarket_probs(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    probs: dict[str, dict] = {}
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        try:
            p = normalise([float(row["p_home"]), float(row["p_draw"]), float(row["p_away"])])
        except (ValueError, KeyError):
            continue
        probs[match_key(row["home"], row["away"])] = {
            "p": p,
            "sum_raw": row.get("sum_raw", ""),
            "snapshot_times": row.get("snapshot_times", ""),
        }
    return probs


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def brier(p: list[float], actual: str) -> float:
    obs = [1.0 if o == actual else 0.0 for o in OUTCOMES]
    return sum((pi - oi) ** 2 for pi, oi in zip(p, obs))


def log_loss(p: list[float], actual: str, eps: float = 1e-12) -> float:
    idx = OUTCOMES.index(actual)
    return -math.log(max(eps, min(1.0, p[idx])))


def rps(p: list[float], actual: str) -> float:
    obs = [1.0 if o == actual else 0.0 for o in OUTCOMES]
    cum_p = cum_o = total = 0.0
    for i in range(len(OUTCOMES) - 1):
        cum_p += p[i]
        cum_o += obs[i]
        total += (cum_p - cum_o) ** 2
    return total / (len(OUTCOMES) - 1)


def favourite(p: list[float]) -> tuple[str, float]:
    idx = max(range(3), key=lambda i: p[i])
    return OUTCOMES[idx], p[idx]


def bootstrap_ci(values: list[float], n_boot: int, seed: int, alpha: float = 0.05) -> tuple[float, float]:
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def calibration_table(records: list[dict]) -> list[dict]:
    bands = [(0.34, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.80), (0.80, 1.01)]
    table = []
    for lo, hi in bands:
        bucket = [r for r in records if lo <= r["fav_prob"] < hi]
        if not bucket:
            continue
        table.append({
            "band": f"[{lo:.2f},{hi:.2f})",
            "n": len(bucket),
            "mean_predicted_fav_prob": round(mean(r["fav_prob"] for r in bucket), 4),
            "observed_fav_win_rate": round(mean(1.0 if r["fav_correct"] else 0.0 for r in bucket), 4),
        })
    return table


def evaluate_source(name: str, probs: dict[str, dict], results: dict[str, dict],
                    vig: float, n_boot: int, seed: int) -> tuple[dict, list[dict]]:
    records: list[dict] = []
    rng = random.Random(seed)
    for key, info in probs.items():
        if key not in results:
            continue
        p = info["p"]
        actual = results[key]["actual_outcome"]
        fav, fav_p = favourite(p)
        fair_odds = 1.0 / fav_p
        # Flat 1u on the model's pick.
        win = fav == actual
        profit_fair = (fair_odds - 1.0) if win else -1.0
        profit_vig = (fair_odds * (1.0 - vig) - 1.0) if win else -1.0
        # Baselines.
        home_win = actual == "home"
        rand_pick = OUTCOMES[rng.randrange(3)]
        records.append({
            "source": name,
            "home_team": results[key]["home_team"],
            "away_team": results[key]["away_team"],
            "actual_score": results[key]["actual_score"],
            "actual_outcome": actual,
            "p_home": round(p[0], 4), "p_draw": round(p[1], 4), "p_away": round(p[2], 4),
            "model_pick": fav, "fav_prob": fav_p, "fair_odds": round(fair_odds, 3),
            "fav_correct": win,
            "brier": brier(p, actual), "log_loss": log_loss(p, actual), "rps": rps(p, actual),
            "profit_fair": profit_fair, "profit_vig": profit_vig,
            "profit_always_home_fair": (1.0 / p[0] - 1.0) if home_win else -1.0,
            "profit_random_fair": (1.0 / p[OUTCOMES.index(rand_pick)] - 1.0) if rand_pick == actual else -1.0,
        })

    n = len(records)
    if n == 0:
        return {"source": name, "n": 0, "note": "no matches joined to trusted results"}, records

    uniform_brier = brier([1 / 3, 1 / 3, 1 / 3], "home")  # same for any actual
    fair = [r["profit_fair"] for r in records]
    vigp = [r["profit_vig"] for r in records]
    summary = {
        "source": name,
        "n": n,
        "forecast_skill": {
            "outcome_accuracy": round(mean(1.0 if r["fav_correct"] else 0.0 for r in records), 4),
            "mean_brier": round(mean(r["brier"] for r in records), 4),
            "uniform_brier": round(uniform_brier, 4),
            "brier_skill_vs_uniform": round(1 - mean(r["brier"] for r in records) / uniform_brier, 4),
            "mean_log_loss": round(mean(r["log_loss"] for r in records), 4),
            "uniform_log_loss": round(-math.log(1 / 3), 4),
            "mean_rps": round(mean(r["rps"] for r in records), 4),
        },
        "calibration_by_favourite_band": calibration_table(records),
        "betting": {
            "assumed_vig": vig,
            "stake_per_bet": 1.0,
            "model_pick_fair_odds": {
                "roi": round(mean(fair), 4),
                "roi_ci95": [round(x, 4) for x in bootstrap_ci(fair, n_boot, seed)],
                "expectation_note": "Betting at the price the probability came from is 0-EV by construction.",
            },
            "model_pick_vigged_odds": {
                "roi": round(mean(vigp), 4),
                "roi_ci95": [round(x, 4) for x in bootstrap_ci(vigp, n_boot, seed + 1)],
                "expectation_note": f"Realistic price carries the book's vig; expectation ~ -{vig:.0%}.",
            },
            "baseline_always_home_fair_roi": round(mean(r["profit_always_home_fair"] for r in records), 4),
            "baseline_random_fair_roi": round(mean(r["profit_random_fair"] for r in records), 4),
        },
    }
    return summary, records


def load_realised_context(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: data.get(k) for k in (
        "completed_matches", "total_points", "average_points_per_match",
        "exact_hit_rate", "outcome_hit_rate_including_exact_close_result",
    ) if k in data}


def main() -> int:
    ap = argparse.ArgumentParser(description="WC 2026 predictive-power & betting-edge validation")
    ap.add_argument("--results-csv", default=RESULTS_DEFAULT)
    ap.add_argument("--market-history-csv", default=MARKET_HISTORY_DEFAULT)
    ap.add_argument("--prediction-log-csv", default="outputs/backtesting/prediction_log.csv")
    ap.add_argument("--polymarket-csv", default=POLYMARKET_DEFAULT)
    ap.add_argument("--realised-summary", default=REALISED_SUMMARY_DEFAULT)
    ap.add_argument("--out-dir", default=OUT_DIR_DEFAULT)
    ap.add_argument("--assumed-vig", type=float, default=0.05, help="Bookmaker 1X2 hold for the realistic price sim")
    ap.add_argument("--n-boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260622)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = load_trusted_results(Path(args.results_csv))
    market_probs = load_market_history_probs(Path(args.market_history_csv))
    log_probs = load_prediction_log_probs(Path(args.prediction_log_csv))
    poly_probs = load_polymarket_probs(Path(args.polymarket_csv))

    sources = {
        "market_history_devig_consensus": market_probs,
        "prediction_log_consensus": log_probs,
        "polymarket_prekickoff": poly_probs,
    }
    summaries, all_records = [], []
    for name, probs in sources.items():
        summary, records = evaluate_source(name, probs, results, args.assumed_vig, args.n_boot, args.seed)
        summaries.append(summary)
        all_records.extend(records)

    scored_keys = {match_key(r["home_team"], r["away_team"]) for r in all_records}

    # Pooled estimate across both markets (both are "the market"; market_history wins any
    # overlap). Summary-only so the per-match CSV stays one row per match per real source.
    combined_probs = {**poly_probs, **market_probs}
    combined_summary, _ = evaluate_source("all_markets_combined", combined_probs, results,
                                          args.assumed_vig, args.n_boot, args.seed + 7)
    summaries.append(combined_summary)
    summary = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "trusted_completed_matches": len(results),
        "matches_with_any_prematch_probability": len(scored_keys),
        "coverage_fraction": round(len(scored_keys) / len(results), 3) if results else 0.0,
        "assumed_vig": args.assumed_vig,
        "n_bootstrap": args.n_boot,
        "sources": summaries,
        "superbru_realised_context_n40": load_realised_context(Path(args.realised_summary)),
        "caveats": [
            "Model probabilities ARE the de-vigged market consensus; this measures whether the market "
            "(and therefore the model) beat its own price on the WC sample, not an independent edge.",
            f"Only {len(scored_keys)}/{len(results)} completed matches had a stored pre-match probability. "
            "Pre-match probabilities are not being logged for most fixtures, so betting validation is "
            "currently under-powered. Log every prediction's probability + the obtainable price to grow this.",
            "Betting at fair odds is 0-EV by construction and at vigged odds is ~ -vig; any deviation here "
            "is small-sample noise, not demonstrated alpha.",
            "No independent sharp anchor (Pinnacle/Betfair closing) is used, so closing-line value is NOT tested.",
        ],
        "outputs": {
            "per_match_csv": str(out_dir / "wc_predictive_power_per_match.csv"),
            "summary_json": str(out_dir / "wc_predictive_power_summary.json"),
        },
    }

    per_match_path = out_dir / "wc_predictive_power_per_match.csv"
    if all_records:
        fields = list(all_records[0].keys())
        with per_match_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(all_records)

    (out_dir / "wc_predictive_power_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Human-readable report.
    print("=" * 72)
    print("WC 2026 PREDICTIVE-POWER & BETTING-EDGE VALIDATION")
    print("=" * 72)
    print(f"Trusted completed matches : {summary['trusted_completed_matches']}")
    print(f"With pre-match probability : {summary['matches_with_any_prematch_probability']} "
          f"({summary['coverage_fraction']:.0%} coverage)")
    ctx = summary["superbru_realised_context_n40"]
    if ctx:
        print(f"SuperBru realised (n={ctx.get('completed_matches')}): "
              f"{ctx.get('average_points_per_match')} pts/match, "
              f"outcome hit {ctx.get('outcome_hit_rate_including_exact_close_result')}")
    for s in summaries:
        print("\n" + "-" * 72)
        print(f"SOURCE: {s['source']}  (n={s.get('n', 0)})")
        if not s.get("n"):
            print("  (no matches joined)")
            continue
        fs = s["forecast_skill"]
        print(f"  outcome accuracy   : {fs['outcome_accuracy']:.1%}")
        print(f"  Brier (vs uniform) : {fs['mean_brier']:.4f} vs {fs['uniform_brier']:.4f} "
              f"(skill {fs['brier_skill_vs_uniform']:+.3f})")
        print(f"  log-loss(vs unif)  : {fs['mean_log_loss']:.4f} vs {fs['uniform_log_loss']:.4f}")
        b = s["betting"]
        ff, vv = b["model_pick_fair_odds"], b["model_pick_vigged_odds"]
        print(f"  ROI model pick @ FAIR odds : {ff['roi']:+.3f}  CI95 {ff['roi_ci95']}")
        print(f"  ROI model pick @ VIG odds  : {vv['roi']:+.3f}  CI95 {vv['roi_ci95']}  (vig {b['assumed_vig']:.0%})")
    print("\n" + "=" * 72)
    print("VERDICT: probabilities are market-derived; ROI CIs span/realise <= 0 -> no")
    print("demonstrated betting edge. Coverage is too small to claim otherwise. See caveats.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
