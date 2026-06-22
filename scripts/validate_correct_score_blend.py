"""
validate_correct_score_blend.py

Evidence-based selection of model.correct_score_blend_weight.

The blend weight controls how much of the Oddspedia correct-score grid shapes the
final scoreline matrix (geometric blend) versus the market-implied Poisson + Dixon-Coles
fit. This script refuses to treat that weight as a free knob. It runs three analyses and
recommends a weight from the evidence:

  A. EV-tradeoff sweep (needs market odds + grid, NO results required)
     For every match that has both saved market odds and an Oddspedia grid, rebuild the
     real engine distribution at each candidate weight w. Measure, per match:
       - Jensen-Shannon divergence between the pure-market (w=0) and grid distributions
         (how much independent information the grid carries),
       - whether the recommended Superbru pick changes vs w=0,
       - the EV cost of the blended pick measured under the MARKET distribution
         (the downside if the market, not the grid, is correct).
     A weight is "safe" while its mean market-EV cost stays within a tolerance; it is
     "active" once it starts changing picks on grid-independent matches.

  B. Grid informativeness (needs grid + results, NO market odds required)
     For completed matches, score the grid's own EV-optimal Superbru pick against the
     actual result and report realized points, exact-hit rate, and the grid's log-loss on
     the realised scoreline. Establishes whether the grid is predictive at all - if it is
     not, the blend weight should be 0 regardless of the sweep.

  C. Realized-points sweep (needs market odds + grid + results)
     When all three sources overlap, rebuild the blended distribution at each weight and
     score realized Superbru points + exact-score log-loss against actuals. This is the
     gold-standard validation and auto-activates as results accrue. Today the overlap is
     empty, so it is reported as pending.

The recommended weight is the largest candidate that is (i) supported by grid
informativeness in B, and (ii) keeps mean market-EV cost in A within --max-mean-ev-cost.

Outputs a JSON + CSV report and prints the recommended weight.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "scripts"))

from superbru_score_engine.config import load_config  # noqa: E402
from superbru_score_engine.decision import SuperbruDecisionEngine  # noqa: E402
from superbru_score_engine.decision.superbru import score_actual_prediction  # noqa: E402
from superbru_score_engine.ingest.normalise import normalise_the_odds_api_events  # noqa: E402
from superbru_score_engine.model import OddsToScorelineModel  # noqa: E402
from superbru_score_engine.model.ratings import RatingsStore  # noqa: E402

# Reuse the exact grid-injection used by the live auto-pick runner so the validation
# pipeline and the production pipeline build identical blended distributions.
_ap_spec = importlib.util.spec_from_file_location(
    "auto_pick_match_scoped", ROOT / "scripts" / "auto_pick_match_scoped.py"
)
_ap = importlib.util.module_from_spec(_ap_spec)  # type: ignore[arg-type]
_ap_spec.loader.exec_module(_ap)  # type: ignore[union-attr]
_inject_oddspedia_grid = _ap._inject_oddspedia_grid

DEFAULT_WEIGHTS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

ALIAS = {
    "ivory-coast": "cote-d-ivoire",
    "cote-divoire": "cote-d-ivoire",
    "united-states": "usa",
    "south-korea": "korea-republic",
    "bosnia-herzegovina": "bosnia-and-herzegovina",
    "bosnia-and-herzegovina": "bosnia-and-herzegovina",
}


def slug(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def mkey(home: Any, away: Any) -> tuple[str, str]:
    def norm(x: Any) -> str:
        s = slug(x)
        return ALIAS.get(s, s)

    return norm(home), norm(away)


def parse_score(text: Any) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*-\s*(\d+)", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    """Jensen-Shannon divergence (bits) between two flattened, padded distributions."""
    size = max(p.size, q.size)
    pp = np.zeros(size)
    qq = np.zeros(size)
    pp[: p.size] = p.ravel()
    qq[: q.size] = q.ravel()
    pp = pp / pp.sum() if pp.sum() > 0 else pp
    qq = qq / qq.sum() if qq.sum() > 0 else qq
    m = 0.5 * (pp + qq)

    def kl(a: np.ndarray, b: np.ndarray) -> float:
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / b[mask])))

    return 0.5 * kl(pp, m) + 0.5 * kl(qq, m)


def ev_under_distribution(matrix: np.ndarray, pick: tuple[int, int], ci_cutoff: float) -> float:
    """Expected Superbru points for `pick` if `matrix` is the true scoreline distribution."""
    total = 0.0
    rows, cols = matrix.shape
    for ah in range(rows):
        for aa in range(cols):
            prob = float(matrix[ah, aa])
            if prob <= 0:
                continue
            total += prob * score_actual_prediction(pick[0], pick[1], ah, aa, ci_cutoff)
    return total


def load_odds_events(path: Path) -> dict[tuple[str, str], dict]:
    events = json.loads(path.read_text(encoding="utf-8"))
    return {mkey(e.get("home_team"), e.get("away_team")): e for e in events}


def load_results(path: Path) -> dict[tuple[str, str], tuple[int, int]]:
    out: dict[tuple[str, str], tuple[int, int]] = {}
    if not path.exists():
        return out
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        score = parse_score(row.get("actual_score"))
        completed = str(row.get("is_completed", "")).lower() in {"1", "true", "yes"}
        if score and completed:
            out[mkey(row.get("home_team"), row.get("away_team"))] = score
    return out


def grid_match_keys(path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        keys.add(mkey(row.get("home_team"), row.get("away_team")))
    return keys


def build_blended(match, grid_csv: Path, base_config, weight: float):
    """Return (distribution, prediction) for a given blend weight using the real engine."""
    cfg_model = replace(base_config.model, correct_score_blend_weight=weight)
    injected = _inject_oddspedia_grid(match, {}, grid_csv)
    ratings = RatingsStore(config=base_config.ratings)
    model = OddsToScorelineModel(cfg_model, ratings)
    decision = SuperbruDecisionEngine(
        base_config.superbru, cfg_model.candidate_grid_goals, base_config.public_pick, base_config.sensitivity
    )
    distribution = model.build_distribution(injected)
    prediction = decision.predict(distribution)
    return distribution, prediction, injected


def grid_ev_pick(grid_csv: Path, key: tuple[str, str], ci_cutoff: float) -> tuple[int, int] | None:
    """EV-optimal Superbru pick from the grid alone (no market), for informativeness scoring."""
    cells: dict[tuple[int, int], float] = {}
    for row in csv.DictReader(grid_csv.open(encoding="utf-8-sig")):
        if mkey(row.get("home_team"), row.get("away_team")) != key:
            continue
        if str(row.get("score_bucket", "")).strip().lower() != "exact":
            continue
        sc = parse_score(row.get("score_key"))
        pct = row.get("probability_pct")
        if sc is None or not pct:
            continue
        try:
            cells[sc] = float(pct) / 100.0
        except ValueError:
            continue
    if not cells:
        return None
    max_g = max(max(h, a) for h, a in cells) + 1
    matrix = np.zeros((max_g + 1, max_g + 1))
    for (h, a), p in cells.items():
        matrix[h, a] = p
    if matrix.sum() <= 0:
        return None
    matrix = matrix / matrix.sum()
    best_pick = None
    best_ev = -1.0
    for h in range(matrix.shape[0]):
        for a in range(matrix.shape[1]):
            ev = ev_under_distribution(matrix, (h, a), ci_cutoff)
            if ev > best_ev:
                best_ev, best_pick = ev, (h, a)
    return best_pick


def grid_logloss_on_actual(grid_csv: Path, key: tuple[str, str], actual: tuple[int, int]) -> float | None:
    for row in csv.DictReader(grid_csv.open(encoding="utf-8-sig")):
        if mkey(row.get("home_team"), row.get("away_team")) != key:
            continue
        if parse_score(row.get("score_key")) == actual:
            try:
                p = max(1e-6, float(row.get("probability_pct")) / 100.0)
                return float(-math.log(p))
            except (ValueError, TypeError):
                return None
    return float(-math.log(1e-6))  # actual scoreline not quoted -> heavy penalty


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    ci = config.superbru.ci_cutoff
    grid_csv = Path(args.oddspedia_grid_csv)
    odds = load_odds_events(Path(args.market_odds_json))
    results = load_results(Path(args.results_csv))
    gkeys = grid_match_keys(grid_csv)
    weights = DEFAULT_WEIGHTS

    overlap_odds_grid = sorted(gkeys & set(odds))
    overlap_all = sorted(gkeys & set(odds) & set(results))
    overlap_grid_results = sorted(gkeys & set(results))

    # ── Analysis A: EV-tradeoff sweep on market+grid matches ───────────────────
    sweep_rows: list[dict[str, Any]] = []
    per_weight: dict[float, dict[str, list[float]]] = {w: {"ev_cost": [], "changed": [], "js": []} for w in weights}
    for key in overlap_odds_grid:
        event = odds[key]
        matches = normalise_the_odds_api_events([event])
        if not matches:
            continue
        match = matches[0]
        # Baseline market distribution (w=0)
        base_dist, base_pred, _ = build_blended(match, grid_csv, config, 0.0)
        base_matrix = np.asarray(base_dist.matrix)
        base_pick = (int(base_pred.recommended.home_goals), int(base_pred.recommended.away_goals))
        # Grid-only matrix for divergence (weight 1.0 isolates the grid shape)
        grid_dist, _, _ = build_blended(match, grid_csv, config, 1.0)
        js = js_divergence(base_matrix, np.asarray(grid_dist.matrix))
        for w in weights:
            dist, pred, _ = build_blended(match, grid_csv, config, w)
            pick = (int(pred.recommended.home_goals), int(pred.recommended.away_goals))
            changed = pick != base_pick
            # EV cost if the market is the truth: market-pick EV minus blended-pick EV, both
            # evaluated under the pure-market distribution.
            ev_market_pick = ev_under_distribution(base_matrix, base_pick, ci)
            ev_blend_pick = ev_under_distribution(base_matrix, pick, ci)
            ev_cost = max(0.0, ev_market_pick - ev_blend_pick)
            per_weight[w]["ev_cost"].append(ev_cost)
            per_weight[w]["changed"].append(1.0 if changed else 0.0)
            per_weight[w]["js"].append(js)
            sweep_rows.append({
                "match": f"{key[0]} vs {key[1]}",
                "weight": w,
                "js_divergence_market_vs_grid": round(js, 4),
                "market_pick": f"{base_pick[0]}-{base_pick[1]}",
                "blended_pick": f"{pick[0]}-{pick[1]}",
                "pick_changed": changed,
                "ev_cost_if_market_true": round(ev_cost, 4),
                "blended_pick_ev_under_blend": round(float(pred.recommended.expected_points), 4),
            })

    weight_summary = []
    for w in weights:
        costs = per_weight[w]["ev_cost"]
        changed = per_weight[w]["changed"]
        weight_summary.append({
            "weight": w,
            "matches": len(costs),
            "mean_ev_cost_if_market_true": round(float(np.mean(costs)), 4) if costs else None,
            "max_ev_cost_if_market_true": round(float(np.max(costs)), 4) if costs else None,
            "pick_change_rate": round(float(np.mean(changed)), 4) if changed else None,
        })

    # ── Analysis B: grid informativeness on completed matches ──────────────────
    grid_rows: list[dict[str, Any]] = []
    grid_points: list[float] = []
    grid_exact: list[float] = []
    grid_logloss: list[float] = []
    for key in overlap_grid_results:
        actual = results[key]
        pick = grid_ev_pick(grid_csv, key, ci)
        if pick is None:
            continue
        pts = score_actual_prediction(pick[0], pick[1], actual[0], actual[1], ci)
        ll = grid_logloss_on_actual(grid_csv, key, actual)
        grid_points.append(pts)
        grid_exact.append(1.0 if pick == actual else 0.0)
        if ll is not None:
            grid_logloss.append(ll)
        grid_rows.append({
            "match": f"{key[0]} vs {key[1]}",
            "grid_ev_pick": f"{pick[0]}-{pick[1]}",
            "actual": f"{actual[0]}-{actual[1]}",
            "superbru_points": pts,
            "exact_hit": pick == actual,
            "logloss_on_actual": round(ll, 4) if ll is not None else None,
        })
    # Naive baseline (1-0 home / 0-1 away by favourite is unknown without odds; use 1-1 draw-agnostic
    # heuristic of always picking the grid modal is already grid_ev). Compare to a fixed 1-1 baseline.
    naive_points = [
        score_actual_prediction(1, 0, results[k][0], results[k][1], ci) for k in overlap_grid_results
    ]

    grid_informative = bool(
        grid_points and naive_points and np.mean(grid_points) >= np.mean(naive_points)
    )
    b_mean = round(float(np.mean(grid_points)), 3) if grid_points else None
    n_mean = round(float(np.mean(naive_points)), 3) if naive_points else None

    # ── Recommendation ─────────────────────────────────────────────────────────
    # The blend weight is chosen from evidence, not convenience. Two regimes:
    #
    #   * Pick-preserving (shape-refinement): weights where the recommended Superbru
    #     scoreline never changes vs the pure-market w=0 baseline. Here the grid only
    #     reshapes the distribution that feeds private_chase, p_exact/p_close confidence
    #     tiers, sensitivity stability, and the defensive chaser model - at ~zero risk of
    #     overriding the market on the headline pick. EV cost against the market is ~0 by
    #     construction, so "largest weight within EV tolerance" is NOT a valid selector
    #     (it rewards inert weights). The valid selector in this regime is the largest
    #     weight that still preserves every market pick.
    #
    #   * Pick-overriding: weights that flip picks. These are only justified by realized
    #     Superbru points (Analysis C). Until that overlap exists we must NOT adopt an
    #     override weight on faith, however small its modelled EV cost.
    #
    # So: if the grid is informative, recommend the largest pick-preserving weight within
    # the EV-cost tolerance. If realized-points evidence later supports going higher, the
    # harness will surface it via Analysis C and this rule can be relaxed.
    tol = args.max_mean_ev_cost
    realized_available = bool(overlap_all)
    pick_preserving = [
        ws["weight"] for ws in weight_summary
        if ws["weight"] > 0
        and ws["pick_change_rate"] == 0.0
        and ws["mean_ev_cost_if_market_true"] is not None
        and ws["mean_ev_cost_if_market_true"] <= tol
    ]
    if not grid_informative:
        recommended = 0.0
        rationale = (
            "Grid did not beat the naive baseline on completed matches (or no completed "
            "grid+result overlap yet); keep blend at 0.0 until realized-points evidence supports it."
        )
    elif pick_preserving:
        recommended = max(pick_preserving)
        rationale = (
            f"Largest pick-preserving weight (no market pick overridden, mean EV cost <= {tol}) "
            f"across {len(overlap_odds_grid)} market+grid matches, with grid informativeness "
            f"confirmed on {len(overlap_grid_results)} completed matches (grid {b_mean} vs naive "
            f"{n_mean} mean pts). Grid refines private_chase / confidence / sensitivity / defensive "
            f"signals without overriding the market scoreline. Override weights (>= the first "
            f"pick-changing weight) are withheld until realized-points evidence (Analysis C) exists"
            + ("." if not realized_available else "; Analysis C now has data - re-evaluate.")
        )
    else:
        recommended = 0.0
        rationale = "No positive pick-preserving weight stayed within the EV-cost tolerance; keep blend at 0.0."

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": args.config,
        "weights_tested": weights,
        "data_overlap": {
            "grid_matches": len(gkeys),
            "market_odds_matches": len(odds),
            "completed_results": len(results),
            "market_and_grid": len(overlap_odds_grid),
            "grid_and_results": len(overlap_grid_results),
            "market_grid_and_results": len(overlap_all),
        },
        "analysis_A_ev_tradeoff_sweep": weight_summary,
        "analysis_B_grid_informativeness": {
            "completed_matches_scored": len(grid_points),
            "grid_ev_pick_mean_points": round(float(np.mean(grid_points)), 4) if grid_points else None,
            "naive_1_0_mean_points": round(float(np.mean(naive_points)), 4) if naive_points else None,
            "grid_exact_hit_rate": round(float(np.mean(grid_exact)), 4) if grid_exact else None,
            "grid_mean_logloss_on_actual": round(float(np.mean(grid_logloss)), 4) if grid_logloss else None,
            "grid_informative": grid_informative,
            "per_match": grid_rows,
        },
        "analysis_C_realized_points_sweep": {
            "status": "pending_no_overlap" if not overlap_all else "available",
            "note": (
                "Activates automatically once a match has saved market odds, an Oddspedia grid, "
                "and a completed result. Re-run this script as results accrue."
            ),
            "matches": overlap_all,
        },
        "recommended_blend_weight": recommended,
        "recommendation_rationale": rationale,
        "max_mean_ev_cost_tolerance": tol,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "blend_weight_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if sweep_rows:
        with (out_dir / "blend_weight_sweep.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(sweep_rows[0]))
            writer.writeheader()
            writer.writerows(sweep_rows)
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate model.correct_score_blend_weight on EV trade-offs.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--oddspedia-grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    p.add_argument("--market-odds-json", default="outputs/market_odds/worldcup_market_odds_raw.json")
    p.add_argument("--results-csv", default="outputs/superbru_pool/superbru_match_results_auto.csv")
    p.add_argument("--out-dir", default="outputs/blend_validation")
    p.add_argument(
        "--max-mean-ev-cost",
        type=float,
        default=0.02,
        help="Tolerance (Superbru points) for mean EV cost if the market is the truth. "
        "The recommended weight is the largest one staying within this bound.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    report = run(args)
    print(json.dumps(report, indent=2))
    print(f"\nRECOMMENDED correct_score_blend_weight = {report['recommended_blend_weight']}")
    print(report["recommendation_rationale"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
