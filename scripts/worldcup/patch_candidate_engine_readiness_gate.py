from pathlib import Path

path = Path("build_polymarket_paper_trade_candidates.py")
text = path.read_text(encoding="utf-8-sig")

if 'READINESS_GATE = Path("outputs/polymarket_model_governance/worldcup_model_readiness_gate.json")' not in text:
    text = text.replace(
        'MODEL_FILE = Path("outputs/polymarket_training/worldcup_fixture_model_probabilities.csv")',
        'MODEL_FILE = Path("outputs/polymarket_training/worldcup_fixture_model_probabilities.csv")\nREADINESS_GATE = Path("outputs/polymarket_model_governance/worldcup_model_readiness_gate.json")'
    )

if "def read_json(path: Path):" not in text:
    text = text.replace(
'''def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))
''',
'''def read_csv(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))

def read_json(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))
'''
    )

if "readiness_gate = read_json(READINESS_GATE)" not in text:
    text = text.replace(
'''prices = read_csv(PRICE_FILE)
model_rows = read_csv(MODEL_FILE)
''',
'''prices = read_csv(PRICE_FILE)
model_rows = read_csv(MODEL_FILE)
readiness_gate = read_json(READINESS_GATE)
paper_gate_enabled = boolish(readiness_gate.get("paper_trading_enabled"))
'''
    )

if 'reason.append("paper_trading_readiness_gate_disabled")' not in text:
    text = text.replace(
'''if market_price is None:
        reason.append("missing_market_price")
''',
'''if not paper_gate_enabled:
        reason.append("paper_trading_readiness_gate_disabled")

    if market_price is None:
        reason.append("missing_market_price")
'''
    )

if "and paper_gate_enabled" not in text:
    text = text.replace(
'''if (
            qinfo["usable"]
            and model_trained
            and model_tradable
            and edge_value >= MIN_EDGE
            and MIN_PRICE <= market_price <= MAX_PRICE
        ):''',
'''if (
            paper_gate_enabled
            and qinfo["usable"]
            and model_trained
            and model_tradable
            and edge_value >= MIN_EDGE
            and MIN_PRICE <= market_price <= MAX_PRICE
        ):'''
    )

if '"paper_trading_gate_enabled": paper_gate_enabled,' not in text:
    text = text.replace(
'''"model_file_exists": MODEL_FILE.exists(),''',
'''"model_file_exists": MODEL_FILE.exists(),
    "readiness_gate_file": str(READINESS_GATE),
    "readiness_gate_exists": READINESS_GATE.exists(),
    "paper_trading_gate_enabled": paper_gate_enabled,'''
    )

path.write_text(text, encoding="utf-8")
print("patched", path)
