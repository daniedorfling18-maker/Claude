from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _script_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_strategy_v2_cycle_pins_repo_source_before_python_invocations():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    pythonpath_index = text.index("$env:PYTHONPATH")
    first_python_module_index = text.index("python -m polymarket_predictive_engine.cli")

    assert "(Resolve-Path .\\src).Path" in text
    assert pythonpath_index < first_python_module_index


def test_strategy_v2_cycle_renders_dashboard_after_latest_status_is_written():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    status_write_index = text.index("strategy_v2_cycle_latest_status.json")
    dashboard_render_index = text.index("render_polymarket_dashboard.py")

    assert status_write_index < dashboard_render_index


def test_strategy_v2_scheduled_wrapper_pins_repo_source():
    text = _script_text("scripts/run_strategy_v2_cycle_scheduled_wrapper.ps1")

    assert '$env:PYTHONPATH = Join-Path $repoRoot "src"' in text
