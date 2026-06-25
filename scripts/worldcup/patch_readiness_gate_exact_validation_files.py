from pathlib import Path

path = Path("build_worldcup_model_readiness_gate.py")
text = path.read_text(encoding="utf-8-sig")

old_completed = 'COMPLETED_VALIDATION_FILES = list(Path("outputs/polymarket_training").glob("completed_game_validation_*.csv"))'
new_completed = 'COMPLETED_VALIDATION_FILES = [Path("outputs/polymarket_training/worldcup_completed_token_validation.csv")]'

old_moneyline = 'MONEYLINE_VALIDATION_FILES = list(Path("outputs/polymarket_training").glob("*moneyline_validation*.csv"))'
new_moneyline = 'MONEYLINE_VALIDATION_FILES = [Path("outputs/polymarket_training/worldcup_completed_moneyline_validation.csv")]'

if old_completed not in text:
    raise SystemExit("Could not find COMPLETED_VALIDATION_FILES glob line")

if old_moneyline not in text:
    raise SystemExit("Could not find MONEYLINE_VALIDATION_FILES glob line")

text = text.replace(old_completed, new_completed)
text = text.replace(old_moneyline, new_moneyline)

path.write_text(text, encoding="utf-8")
print("patched readiness gate to use canonical completed validation files only")
