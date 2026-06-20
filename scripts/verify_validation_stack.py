from __future__ import annotations

import py_compile
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
        py_compile.compile(str(path), doraise=True)
        print(f"OK {file_name}")

    if missing:
        raise FileNotFoundError("Missing files: " + ", ".join(missing))

    print("Validation stack syntax check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
