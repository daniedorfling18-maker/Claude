from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timezone

PRICE_FILE = Path("outputs/polymarket_training/worldcup_fixture_moneyline_clob_prices.csv")

OUT = Path("outputs/polymarket_training/worldcup_fixture_model_probabilities.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_fixture_model_probabilities_summary.json")

def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

rows = read_csv(PRICE_FILE)

yes_rows = [r for r in rows if r.get("outcome") == "Yes"]

by_fixture = {}
for r in yes_rows:
    by_fixture.setdefault(r.get("fixture_slug", ""), []).append(r)

out = []

for fixture_slug, fx_rows in sorted(by_fixture.items()):
    prices = []
    for r in fx_rows:
        p = fnum(r.get("effective_price"))
        if p is not None:
            prices.append((r, p))

    raw_sum = sum(p for _, p in prices)

    quality_flags = []
    if len(prices) != 3:
        quality_flags.append("missing_moneyline_leg")
    if raw_sum < 0.95 or raw_sum > 1.05:
        quality_flags.append("probability_sum_outlier")
    if any(r.get("extraction_quality") != "ok" for r, _ in prices):
        quality_flags.append("extraction_warning")

    usable = not quality_flags

    for r, p in prices:
        if raw_sum > 0:
            model_probability = p / raw_sum
        else:
            model_probability = ""

        out.append({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "fixture_slug": fixture_slug,
            "fixture_date": r.get("fixture_date", ""),
            "outcome_name": r.get("team_or_draw", ""),
            "token_id": r.get("token_id", ""),
            "market_probability": p,
            "model_probability": model_probability,
            "model_version": "v0_shadow_market_copy",
            "model_family": "shadow_baseline",
            "trained": "False",
            "tradable": "False",
            "quality_flags": "|".join(quality_flags) if quality_flags else "ok",
            "governance_reason": "Shadow model copies market probability for plumbing only; not trained and not tradable.",
        })

OUT.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "generated_at_utc",
    "fixture_slug",
    "fixture_date",
    "outcome_name",
    "token_id",
    "market_probability",
    "model_probability",
    "model_version",
    "model_family",
    "trained",
    "tradable",
    "quality_flags",
    "governance_reason",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out)

summary = {
    "input_yes_rows": len(yes_rows),
    "model_probability_rows": len(out),
    "model_version": "v0_shadow_market_copy",
    "trained": False,
    "tradable": False,
    "output": str(OUT),
    "warning": "This model copies market probabilities only. It is for plumbing tests and must not trigger live or paper edge trades.",
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
