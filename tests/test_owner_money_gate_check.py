from pathlib import Path


def test_owner_money_gate_check_includes_effective_config() -> None:
    text = Path("docs/OWNER_CHECKS.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "polymarket_predictive_config.example.yaml" in text
    assert "separately deployed config" in normalized
    assert "effective code-plus-config policy decides money" in normalized
