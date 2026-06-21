from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REQUIRED_PREDICTION_COLUMNS = ["commence_time", "home_team", "away_team", "recommended_scoreline"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full leader-protection decision workflow: base Monte Carlo, stress test, "
            "high-N confirmation, final pick policy, manifest and report."
        )
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--odds-json", default="data/odds_snapshot_real.json")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--leaderboard-csv", default="inputs/pool_leaderboard.csv")
    parser.add_argument("--chaser-profiles-csv", default="inputs/chaser_profiles.csv")
    parser.add_argument("--leader-player", default="Danie")
    parser.add_argument("--chasers", default="Dheben,Bijal,Peter,rob87,Leonard,Kea")
    parser.add_argument("--base-simulations", type=int, default=100_000)
    parser.add_argument("--stress-simulations", type=int, default=100_000)
    parser.add_argument("--confirmation-simulations", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=20260620)
    parser.add_argument("--support-threshold", type=float, default=0.70)
    parser.add_argument("--exclude-match", action="append", default=[])
    parser.add_argument("--max-ev-loss-per-switch", type=float, default=0.025)
    parser.add_argument("--max-total-ev-loss", type=float, default=0.10)
    parser.add_argument("--min-lead-prob-gain", type=float, default=0.005)
    parser.add_argument("--high-risk-min-lead-prob-gain", type=float, default=0.015)
    parser.add_argument("--different-outcome-min-gain", type=float, default=0.020)
    parser.add_argument("--min-z", type=float, default=2.0)
    parser.add_argument("--out-dir", default="outputs/final_leader_decision")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Do not rerun MC/stress scripts; reuse existing subfolder outputs under --out-dir.",
    )
    return parser


def rel(path: str | Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except Exception:
        return str(path)


def sha256_file(path: str | Path) -> str:
    path = Path(path)
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_text(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()
    except Exception:
        return ""


def git_state() -> dict[str, Any]:
    sha = run_text(["git", "rev-parse", "HEAD"])
    branch = run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    status = run_text(["git", "status", "--porcelain"])
    return {
        "commit_sha": sha,
        "branch": branch,
        "working_tree_clean": status == "",
        "status_porcelain": status,
    }


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def switch_key(item: dict[str, Any] | pd.Series) -> tuple[str, str, str, str]:
    return (
        str(item.get("home_team", "")).strip(),
        str(item.get("away_team", "")).strip(),
        str(item.get("raw_pick", "")).strip(),
        str(item.get("candidate_pick", "")).strip(),
    )


def call_script(script_path: str, args: argparse.Namespace, simulations: int, out_dir: Path, *, stress: bool = False) -> None:
    command = [
        sys.executable,
        script_path,
        "--config",
        args.config,
        "--fixtures",
        args.fixtures,
        "--odds-json",
        args.odds_json,
        "--predictions-csv",
        args.predictions_csv,
        "--leaderboard-csv",
        args.leaderboard_csv,
        "--chaser-profiles-csv",
        args.chaser_profiles_csv,
        "--leader-player",
        args.leader_player,
        "--chasers",
        args.chasers,
        "--simulations",
        str(simulations),
        "--seed",
        str(args.seed),
        "--max-ev-loss-per-switch",
        str(args.max_ev_loss_per_switch),
        "--max-total-ev-loss",
        str(args.max_total_ev_loss),
        "--min-lead-prob-gain",
        str(args.min_lead_prob_gain),
        "--high-risk-min-lead-prob-gain",
        str(args.high_risk_min_lead_prob_gain),
        "--different-outcome-min-gain",
        str(args.different_outcome_min_gain),
        "--min-z",
        str(args.min_z),
        "--out-dir",
        str(out_dir),
    ]
    for item in args.exclude_match:
        command.extend(["--exclude-match", item])
    if stress:
        command.extend(["--support-threshold", str(args.support_threshold)])

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")

    print("Running:", " ".join(command))
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def quality_gates(args: argparse.Namespace) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []

    for label, path_text in [
        ("config", args.config),
        ("fixtures", args.fixtures),
        ("odds_json", args.odds_json),
        ("predictions_csv", args.predictions_csv),
        ("leaderboard_csv", args.leaderboard_csv),
        ("chaser_profiles_csv", args.chaser_profiles_csv),
    ]:
        path = ROOT / path_text
        gates.append({"gate": f"input_exists:{label}", "passed": path.exists(), "path": rel(path)})

    predictions_path = ROOT / args.predictions_csv
    if predictions_path.exists():
        try:
            predictions = pd.read_csv(predictions_path)
            missing = [col for col in REQUIRED_PREDICTION_COLUMNS if col not in predictions.columns]
            gates.append({"gate": "predictions_required_columns", "passed": not missing, "missing_columns": missing})
            gates.append({"gate": "predictions_non_empty", "passed": len(predictions) > 0, "row_count": int(len(predictions))})
        except Exception as exc:
            gates.append({"gate": "predictions_readable", "passed": False, "error": str(exc)})
    else:
        gates.append({"gate": "predictions_readable", "passed": False, "error": "predictions CSV missing"})

    return gates


def manifest(args: argparse.Namespace, out_dir: Path, produced_files: list[Path]) -> dict[str, Any]:
    input_paths = {
        "config": args.config,
        "fixtures": args.fixtures,
        "odds_json": args.odds_json,
        "predictions_csv": args.predictions_csv,
        "leaderboard_csv": args.leaderboard_csv,
        "chaser_profiles_csv": args.chaser_profiles_csv,
    }
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "command": " ".join(sys.argv),
        "args": vars(args),
        "git": git_state(),
        "inputs": {
            name: {"path": rel(ROOT / path), "sha256": sha256_file(ROOT / path)} for name, path in input_paths.items()
        },
        "outputs": {rel(path): sha256_file(path) for path in produced_files if path.exists()},
        "out_dir": rel(out_dir),
    }


def build_final_picks(
    confirmation_picks: pd.DataFrame,
    base_summary: dict[str, Any],
    stress_support: pd.DataFrame,
    confirmation_summary: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    base_keys = {switch_key(item): item for item in base_summary.get("accepted_switches", [])}
    confirmation_keys = {switch_key(item): item for item in confirmation_summary.get("accepted_switches", [])}

    robust_keys: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if not stress_support.empty:
        for _, row in stress_support.iterrows():
            if bool_value(row.get("robust_switch")):
                robust_keys[switch_key(row)] = row.to_dict()

    accepted_keys = sorted(set(base_keys) & set(confirmation_keys) & set(robust_keys))
    accepted_policy_switches = []
    for key in accepted_keys:
        payload = dict(confirmation_keys[key])
        payload["stress_support"] = robust_keys[key]
        payload["policy"] = "accepted because base MC, stress test and high-N confirmation all agree"
        accepted_policy_switches.append(payload)

    rejected = []
    for source, switches in [("base_mc", base_keys), ("confirmation_mc", confirmation_keys), ("stress_robust", robust_keys)]:
        for key, payload in switches.items():
            if key not in accepted_keys:
                item = dict(payload)
                item["source"] = source
                item["policy_reject_reason"] = "not supported by all of base MC, stress test and high-N confirmation"
                rejected.append(item)

    final = confirmation_picks.copy()
    final["final_pick"] = final["raw_pick"]
    final["final_switched"] = False
    final["final_policy_reason"] = "raw pick retained; no robust defensive switch approved"

    for home, away, raw_pick, candidate_pick in accepted_keys:
        mask = (
            (final["home_team"].astype(str).str.strip() == home)
            & (final["away_team"].astype(str).str.strip() == away)
            & (final["raw_pick"].astype(str).str.strip() == raw_pick)
        )
        final.loc[mask, "final_pick"] = candidate_pick
        final.loc[mask, "final_switched"] = True
        final.loc[mask, "final_policy_reason"] = "defensive switch approved by full robustness policy"

    return final, accepted_policy_switches, rejected


def write_markdown_report(path: Path, report: dict[str, Any], final_picks: pd.DataFrame) -> None:
    lines: list[str] = []
    lines.append("# Final Leader Decision Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at_utc']}")
    lines.append("")
    lines.append("## Decision")
    lines.append("")
    lines.append(f"**Final policy:** {report['final_policy']}")
    lines.append(f"**Approved defensive switches:** {len(report['accepted_policy_switches'])}")
    lines.append(f"**Rejected defensive switches:** {len(report['rejected_policy_switches'])}")
    lines.append("")
    lines.append("## Monte Carlo Summary")
    lines.append("")
    lines.append("| Run | Simulations | Baseline P(first) | Final P(first) | Delta | P(first/tied) | Accepted switches | Total EV loss |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for label, summary in report["summaries"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(summary.get("simulations", "")),
                    f"{float(summary.get('baseline_p_finish_first', 0.0)):.6f}",
                    f"{float(summary.get('final_p_finish_first', 0.0)):.6f}",
                    f"{float(summary.get('delta_p_finish_first', 0.0)):.6f}",
                    f"{float(summary.get('final_p_finish_first_or_tied', 0.0)):.6f}",
                    str(len(summary.get("accepted_switches", []))),
                    f"{float(summary.get('total_ev_loss', 0.0)):.6f}",
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("## Final Picks")
    lines.append("")
    lines.append("| Kickoff | Home | Away | Raw pick | Final pick | Switched | Risk | Confidence |")
    lines.append("|---|---|---|---|---|---:|---|---|")
    for _, row in final_picks.iterrows():
        lines.append(
            f"| {row.get('commence_time', '')} | {row.get('home_team', '')} | {row.get('away_team', '')} | "
            f"{row.get('raw_pick', '')} | {row.get('final_pick', '')} | {row.get('final_switched', '')} | "
            f"{row.get('risk_tier', '')} | {row.get('confidence_tier', '')} |"
        )
    lines.append("")
    lines.append("## Quality Gates")
    lines.append("")
    for gate in report["quality_gates"]:
        status = "PASS" if gate.get("passed") else "FAIL"
        lines.append(f"- **{status}**: `{gate.get('gate')}`")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "Use the final pick column. A defensive switch is only accepted when base Monte Carlo, stress testing "
        "and high-N confirmation all support the same switch. Otherwise the raw expected-value pick is retained."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    gates = quality_gates(args)
    failed = [gate for gate in gates if not gate.get("passed")]
    if failed:
        gates_path = out_dir / "quality_gates.json"
        gates_path.write_text(json.dumps(gates, indent=2), encoding="utf-8")
        print(json.dumps(gates, indent=2))
        raise SystemExit(f"Quality gates failed. See {rel(gates_path)}")

    base_dir = out_dir / "base_mc"
    stress_dir = out_dir / "stress"
    confirmation_dir = out_dir / "confirmation_500k"

    if not args.reuse_existing:
        call_script("scripts/run_leaderboard_monte_carlo.py", args, args.base_simulations, base_dir)
        call_script("scripts/run_leaderboard_mc_stress.py", args, args.stress_simulations, stress_dir, stress=True)
        call_script("scripts/run_leaderboard_monte_carlo.py", args, args.confirmation_simulations, confirmation_dir)

    base_summary = load_json(base_dir / "leader_mc_summary.json")
    confirmation_summary = load_json(confirmation_dir / "leader_mc_summary.json")
    stress_summary = load_json(stress_dir / "stress_summary.json")
    stress_support_path = stress_dir / "stress_switch_support.csv"
    stress_support = pd.read_csv(stress_support_path).fillna("") if stress_support_path.exists() else pd.DataFrame()
    confirmation_picks = pd.read_csv(confirmation_dir / "leader_mc_picks.csv").fillna("")

    final_picks, accepted_policy_switches, rejected_policy_switches = build_final_picks(
        confirmation_picks=confirmation_picks,
        base_summary=base_summary,
        stress_support=stress_support,
        confirmation_summary=confirmation_summary,
    )

    final_picks_path = out_dir / "final_picks.csv"
    report_json_path = out_dir / "final_decision_report.json"
    report_md_path = out_dir / "final_decision_report.md"
    gates_path = out_dir / "quality_gates.json"
    manifest_path = out_dir / "run_manifest.json"

    final_picks.to_csv(final_picks_path, index=False)
    gates_path.write_text(json.dumps(gates, indent=2), encoding="utf-8")

    generated_at = datetime.now(timezone.utc).isoformat()
    report = {
        "generated_at_utc": generated_at,
        "leader_player": args.leader_player,
        "final_policy": "raw EV pick unless base MC, stress test and high-N confirmation all approve the same switch",
        "accepted_policy_switches": accepted_policy_switches,
        "rejected_policy_switches": rejected_policy_switches,
        "summaries": {
            "base_mc": base_summary,
            "confirmation_500k": confirmation_summary,
        },
        "stress": {
            "scenario_count": stress_summary.get("scenario_count"),
            "support_threshold": stress_summary.get("support_threshold"),
            "robust_switch_count": int(len(stress_support[stress_support["robust_switch"].map(bool_value)])) if not stress_support.empty and "robust_switch" in stress_support.columns else 0,
        },
        "quality_gates": gates,
        "outputs": {
            "final_picks_csv": rel(final_picks_path),
            "final_decision_report_md": rel(report_md_path),
            "final_decision_report_json": rel(report_json_path),
            "run_manifest_json": rel(manifest_path),
        },
    }
    report_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown_report(report_md_path, report, final_picks)

    produced = [
        final_picks_path,
        report_json_path,
        report_md_path,
        gates_path,
        manifest_path,
        base_dir / "leader_mc_picks.csv",
        base_dir / "leader_mc_candidate_tests.csv",
        base_dir / "leader_mc_summary.json",
        stress_dir / "stress_picks_by_scenario.csv",
        stress_dir / "stress_candidate_tests.csv",
        stress_dir / "stress_scenario_summary.csv",
        stress_dir / "stress_switch_support.csv",
        stress_dir / "stress_summary.json",
        confirmation_dir / "leader_mc_picks.csv",
        confirmation_dir / "leader_mc_candidate_tests.csv",
        confirmation_dir / "leader_mc_summary.json",
    ]
    manifest_path.write_text(json.dumps(manifest(args, out_dir, produced), indent=2), encoding="utf-8")

    print()
    print("Final decision")
    print(json.dumps({
        "accepted_policy_switches": accepted_policy_switches,
        "final_picks_csv": rel(final_picks_path),
        "report": rel(report_md_path),
        "manifest": rel(manifest_path),
    }, indent=2))
    print()
    print(final_picks[["commence_time", "home_team", "away_team", "raw_pick", "final_pick", "final_switched", "risk_tier", "confidence_tier"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
