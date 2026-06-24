from pathlib import Path

src = Path("backfill_worldcup_historical_from_condition_registry.py")
dst = Path("backfill_worldcup_historical_from_condition_registry_closed_only.py")

text = src.read_text(encoding="utf-8-sig")

old = '''tokens = market.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []

    enriched_rows.append({'''

new = '''tokens = market.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []

    is_closed = str(market.get("closed", "")).strip().lower() == "true"
    winner_tokens = [
        t for t in tokens
        if str(t.get("winner", "")).strip().lower() == "true"
    ]

    if not is_closed:
        errors.append({
            "condition_id": condition_id,
            "stage": "closed_market_filter",
            "error": "market_not_closed",
        })
        continue

    if len(winner_tokens) != 1:
        errors.append({
            "condition_id": condition_id,
            "stage": "winner_integrity_filter",
            "error": f"expected_one_winner_token_found_{len(winner_tokens)}",
        })
        continue

    enriched_rows.append({'''

if old not in text:
    raise SystemExit("Patch anchor not found. Do not continue; inspect the script manually.")

text = text.replace(old, new)

text = text.replace(
'worldcup_historical_clob_enriched_condition_registry.csv',
'worldcup_historical_clob_enriched_condition_registry_closed_only.csv'
)

text = text.replace(
'worldcup_historical_clob_moneyline_labels.csv',
'worldcup_historical_clob_moneyline_labels_closed_only.csv'
)

text = text.replace(
'worldcup_historical_clob_moneyline_price_history.csv',
'worldcup_historical_clob_moneyline_price_history_closed_only.csv'
)

text = text.replace(
'worldcup_historical_clob_registry_backfill_summary.json',
'worldcup_historical_clob_registry_backfill_closed_only_summary.json'
)

dst.write_text(text, encoding="utf-8")
print(f"wrote {dst}")
