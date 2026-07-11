from __future__ import annotations

from pathlib import Path

FILES = [
    "scripts/convert_smartbet_grids_to_calibration.py",
    "scripts/normalise_old_oddspedia_grids.py",
    "scripts/fetch_market_odds_theoddsapi.py",
    "scripts/build_market_odds_validation.py",
    "scripts/update_market_odds_history.py",
    "scripts/run_component_validation_rescore.py",
    "scripts/build_final_locked_picks.py",
    "scripts/build_daily_robust_card.py",
    "scripts/build_predictions_from_locked_card.py",
    "scripts/notify_score_changes.py",
    "scripts/build_pick_validation_report.py",
    "scripts/run_calibration_diagnostics.py",
    "scripts/run_final_leader_decision.py",
]


def main() -> int:
    missing: list[str] = []
    for file_name in FILES:
        path = Path(file_name)
        if not path.exists():
            missing.append(file_name)
            continue
        # Syntax-check without writing bytecode: py_compile writes .pyc into
        # scripts/__pycache__, which is read-only inside the VPS containers
        # (./scripts is bind-mounted :ro) and killed the locked-card chain
        # with OSError 30. compile() raises the same SyntaxError and touches
        # nothing on disk.
        # utf-8-sig strips the BOM some of these files carry (py_compile's
        # tokenizer did the same silently).
        compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        print(f"OK {file_name}")

    if missing:
        raise FileNotFoundError("Missing files: " + ", ".join(missing))

    print("Validation stack syntax check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
