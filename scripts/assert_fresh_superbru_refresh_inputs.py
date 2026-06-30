from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_INPUT_FRAGMENTS = [
    "inputs/pool_leaderboard.csv",
    "inputs/chaser_profiles.csv",
    "inputs/manual_match_flags.csv",
    "data/odds_snapshot_real.json",
]

REQUIRED_FILES = {
    "live_leaderboard": "outputs/superbru_pool/live_pool_leaderboard.csv",
    "live_chaser_profiles": "outputs/superbru_pool/live_chaser_profiles.csv",
    "live_chasers": "outputs/superbru_pool/live_chasers.txt",
    "fresh_odds_json": "outputs/market_odds/worldcup_market_odds_raw.json",
    "fresh_odds_flat": "outputs/market_odds/worldcup_market_odds_flat.csv",
    "generated_fixtures": "data/fixtures_real.csv",
    "fresh_predictions": "outputs/latest/predictions.csv",
    "final_picks": "outputs/final_leader_decision_round_summary_profiles/final_picks.csv",
    "final_decision_report": "outputs/final_leader_decision_round_summary_profiles/final_decision_report.json",
    "final_decision_manifest": "outputs/final_leader_decision_round_summary_profiles/run_manifest.json",
    "locked_card": "outputs/final_locked_picks/superbru_final_card.csv",
    "daily_robust_card": "outputs/daily_robust_card/daily_robust_superbru_card.csv",
}


def rel(path: str | Path) -> str:
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def txt(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def assert_file(path_text: str, label: str, errors: list[str], *, min_rows: int | None = None) -> None:
    path = ROOT / path_text
    if not path.exists():
        fail(errors, f"Missing {label}: {path_text}")
        return
    if path.is_file() and path.stat().st_size == 0:
        fail(errors, f"Empty {label}: {path_text}")
        return
    if min_rows is not None:
        try:
            rows = read_csv_rows(path)
        except Exception as exc:
            fail(errors, f"Unreadable CSV {label}: {path_text}: {exc}")
            return
        if len(rows) < min_rows:
            fail(errors, f"CSV {label} has {len(rows)} rows; expected at least {min_rows}: {path_text}")


def assert_manifest_is_live(path_text: str, errors: list[str]) -> dict[str, Any]:
    path = ROOT / path_text
    if not path.exists():
        fail(errors, f"Missing manifest: {path_text}")
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(errors, f"Could not read manifest JSON {path_text}: {exc}")
        return {}

    blob = json.dumps(manifest, sort_keys=True)
    for forbidden in FORBIDDEN_INPUT_FRAGMENTS:
        if forbidden in blob:
            fail(errors, f"Forbidden stale input reference found in manifest: {forbidden}")

    required_live_inputs = [
        "outputs/superbru_pool/live_pool_leaderboard.csv",
        "outputs/superbru_pool/live_chaser_profiles.csv",
        "outputs/market_odds/worldcup_market_odds_raw.json",
        "outputs/latest/predictions.csv",
        "data/fixtures_real.csv",
    ]
    for required in required_live_inputs:
        if required not in blob:
            fail(errors, f"Manifest does not reference required live input: {required}")
    return manifest


def assert_live_chasers_match_profiles(errors: list[str]) -> None:
    chasers_path = ROOT / "outputs/superbru_pool/live_chasers.txt"
    profiles_path = ROOT / "outputs/superbru_pool/live_chaser_profiles.csv"
    leaderboard_path = ROOT / "outputs/superbru_pool/live_pool_leaderboard.csv"
    if not chasers_path.exists() or not profiles_path.exists() or not leaderboard_path.exists():
        return

    chasers = [item.strip() for item in chasers_path.read_text(encoding="utf-8").split(",") if item.strip()]
    profiles = read_csv_rows(profiles_path)
    leaderboard = read_csv_rows(leaderboard_path)
    profile_names = {txt(row.get("player")) for row in profiles}
    leaderboard_names = {txt(row.get("player")) for row in leaderboard}

    if not chasers:
        fail(errors, "live_chasers.txt is empty")
    for name in chasers:
        if name not in profile_names:
            fail(errors, f"Live chaser {name!r} is missing from live_chaser_profiles.csv")
        if name not in leaderboard_names:
            fail(errors, f"Live chaser {name!r} is missing from live_pool_leaderboard.csv")


def assert_report_uses_live(report_path_text: str, errors: list[str]) -> dict[str, Any]:
    path = ROOT / report_path_text
    if not path.exists():
        fail(errors, f"Missing final decision report: {report_path_text}")
        return {}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(errors, f"Could not read final decision report {report_path_text}: {exc}")
        return {}

    blob = json.dumps(report, sort_keys=True)
    for forbidden in FORBIDDEN_INPUT_FRAGMENTS:
        if forbidden in blob:
            fail(errors, f"Forbidden stale input reference found in final decision report: {forbidden}")
    return report


def write_audit(errors: list[str], mode: str) -> None:
    out_path = ROOT / "outputs/superbru_pool/freshness_audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "status": "failed" if errors else "ok",
        "errors": errors,
        "required_files": REQUIRED_FILES,
        "forbidden_input_fragments": FORBIDDEN_INPUT_FRAGMENTS,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail closed if a SuperBru refresh run uses stale/static inputs.")
    parser.add_argument("--mode", choices=["pre-odds", "pre-commit"], default="pre-commit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    errors: list[str] = []

    # Live SuperBru state must be established before spending odds credits.
    assert_file("outputs/superbru_pool/live_pool_leaderboard.csv", "live leaderboard", errors, min_rows=1)
    assert_file("outputs/superbru_pool/live_chaser_profiles.csv", "live chaser profiles", errors, min_rows=1)
    assert_file("outputs/superbru_pool/live_chasers.txt", "live chaser list", errors)
    assert_live_chasers_match_profiles(errors)

    if args.mode == "pre-commit":
        for label, path_text in REQUIRED_FILES.items():
            min_rows = 1 if path_text.endswith(".csv") else None
            assert_file(path_text, label, errors, min_rows=min_rows)
        assert_manifest_is_live("outputs/final_leader_decision_round_summary_profiles/run_manifest.json", errors)
        assert_report_uses_live("outputs/final_leader_decision_round_summary_profiles/final_decision_report.json", errors)

    write_audit(errors, args.mode)
    if errors:
        raise SystemExit("Freshness audit failed; refusing to continue with stale SuperBru inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
