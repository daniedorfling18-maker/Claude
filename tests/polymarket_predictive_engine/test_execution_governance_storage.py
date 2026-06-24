from pathlib import Path
import pytest

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.execution.live import live_trade
from polymarket_predictive_engine.governance import governance_report
from polymarket_predictive_engine.storage import init_db, TABLES


def make_cfg(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "polymarket_predictive_config.example.yaml").read_text()
    text = text.replace('output_root: "outputs"', f'output_root: "{(tmp_path/"outputs").as_posix()}"')
    text = text.replace('database_path: "work/polymarket/polymarket_engine.sqlite"', f'database_path: "{(tmp_path/"db.sqlite").as_posix()}"')
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_live_executor_fails_closed(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    with pytest.raises(RuntimeError):
        live_trade(cfg)


def test_governance_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = make_cfg(tmp_path)
    cfg = load_config(cfg_path)
    payload = governance_report(cfg)
    assert not payload["live_trading_approved"]
    assert (tmp_path / "docs" / "POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md").exists()


def test_storage_schema_creation(tmp_path):
    db = tmp_path / "db.sqlite"
    init_db(db)
    assert db.exists()
