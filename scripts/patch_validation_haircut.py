from pathlib import Path
import re

path = Path("scripts/build_pick_validation_report.py")
text = path.read_text(encoding="utf-8")

new_function = r'''def calibration_summary(calibration: dict[str, Any]) -> dict[str, Any]:
    if not calibration:
        return {
            "calibration_available": False,
            "matched_matches": 0,
            "exact_hit_rate": None,
            "outcome_hit_rate": None,
            "exact_brier": None,
            "outcome_brier": None,
            "calibration_haircut": 0.12,
        }

    matched_matches = int(fnum(calibration.get("matched_matches"), 0.0))
    outcome_hit = fnum(calibration.get("outcome_hit_rate"), 0.0)
    exact_hit = fnum(calibration.get("exact_hit_rate"), 0.0)
    outcome_brier = calibration.get("outcome_brier")
    exact_brier = calibration.get("exact_brier")

    # Conservative calibration penalty:
    # poor calibration should not improve confidence versus missing calibration.
    haircut = 0.0

    if matched_matches < 25:
        haircut += 0.04
    if outcome_hit < 0.45:
        haircut += 0.08
    if exact_hit < 0.08:
        haircut += 0.04
    if outcome_brier is not None and fnum(outcome_brier) > 0.25:
        haircut += 0.08
    if exact_brier is not None and fnum(exact_brier) > 0.16:
        haircut += 0.03

    return {
        "calibration_available": True,
        "matched_matches": matched_matches,
        "exact_hit_rate": exact_hit,
        "outcome_hit_rate": outcome_hit,
        "exact_brier": exact_brier,
        "outcome_brier": outcome_brier,
        "calibration_haircut": min(0.20, haircut),
    }


'''

pattern = r"def calibration_summary\(calibration: dict\[str, Any\]\) -> dict\[str, Any\]:\n.*?\n\ndef join_predictions"
replacement = new_function + "def join_predictions"

updated, count = re.subn(pattern, replacement, text, flags=re.S)

if count != 1:
    raise SystemExit(f"Patch failed. Replacements made: {count}")

path.write_text(updated, encoding="utf-8")
print("Patched calibration_summary() successfully.")
