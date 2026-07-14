from pathlib import Path
import pytest

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.execution.live import live_trade
from polymarket_predictive_engine.governance import GOVERNANCE_DOCUMENT_NAMES, governance_report
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
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    for name in GOVERNANCE_DOCUMENT_NAMES:
        (docs_dir / name).write_text(f"controlled source: {name}\n", encoding="utf-8")
    source_before = {name: (docs_dir / name).read_bytes() for name in GOVERNANCE_DOCUMENT_NAMES}
    cfg = load_config(cfg_path)
    payload = governance_report(cfg)
    assert not payload["live_trading_approved"]
    assert payload["status"] == "ok"
    assert payload["docs_created"] == []
    assert payload["docs_verified"] == sorted(GOVERNANCE_DOCUMENT_NAMES)
    assert payload["source_docs_mutated"] is False
    assert len(payload["document_manifest"]) == len(GOVERNANCE_DOCUMENT_NAMES)
    assert source_before == {name: (docs_dir / name).read_bytes() for name in GOVERNANCE_DOCUMENT_NAMES}
    assert (cfg.governance_root / "governance_report_summary.json").exists()


def test_storage_schema_creation(tmp_path):
    db = tmp_path / "db.sqlite"
    init_db(db)
    assert db.exists()
