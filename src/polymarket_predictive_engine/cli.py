from __future__ import annotations

import argparse
import json
import sys

from .active_window_plan import build_active_window_plan
from .algo.replay import run_replay
from .algo.sweep import run_algo_sweep
from .anchored_edge import run as run_anchored_edge
from .backtest import backtest
from .closing_line import build_closing_line_value
from .collection_only import run_collection_only
from .config import config_check, load_config
from .collection_coverage import build_collection_coverage
from .crypto_fundamental import build_crypto_fundamental
from .crypto_targets import build_crypto_targets
from .crypto_updown_labels import build_crypto_updown_proxy_labels
from .data_inventory import inventory
from .data_quality import data_quality
from .dutch_arb_monitor import run_dutch_arb_monitor
from .edge_attribution import build_edge_attribution
from .evidence_history import append_evidence_history
from .execution.live import live_trade
from .execution.paper import paper_trade_report
from .external_feed_collector import collect_external_feeds
from .external_signals import normalize_external_signals
from .family_calibration import build_family_calibration_scorecard
from .features import build_features
from .features_v2 import build_features_v2
from .goal_planner import build_goal_plan
from .governance import governance_report
from .historical_backfill import historical_backfill
from .labels import build_labels
from .live_mispricing import run_live_mispricing, scan_live_mispricing
from .longshot_bias import build_longshot_bias_scan
from .market_making_pnl import evaluate_market_making
from .mispricing_alpha import apply_mispricing_alpha, train_mispricing_alpha_model
from .models.calibrated import load_prediction_models, train_model, write_predictions
from .models.calibration_v2 import train_calibration_model
from .models.category_calibration import train_category_calibration
from .models.optimized import train_optimized_model
from .models.skill_model import train_skill_model
from .overnight_collection import run_collection_only_overnight
from .paper_cycle import run_paper_cycle
from .paper_edge_simulator import simulate_paper_edge
from .paper_session import run_paper_session
from .pipeline_health import pipeline_health
from .pipeline_inventory import pipeline_inventory
from .portfolio import portfolio_snapshot, reconciliation_report
from .price_action_feedback import build_price_action_feedback
from .price_action_microstructure import build_microstructure_edge_lab
from .price_action_model import train_price_action_model
from .price_action_scout import build_price_action_scout
from .price_action_signals import build_price_action_paper_signals
from .price_history_collector import collect_price_history
from .profit_sprint import build_profit_sprint
from .promotion_review import build_promotion_review
from .readiness import paper_live_promotion_gate, paper_trade_readiness, readiness_decision
from .refresh_governance import refresh_governance
from .resolution_collector import collect_resolutions
from .runtime_lock import runtime_lock
from .sharp_anchor import build_sharp_anchor
from .sharp_odds_fetch import fetch_sharp_odds
from .snapshot_ingest import ingest_scanner_snapshot
from .snapshot_label_collector import collect_snapshot_labels
from .storage import init_db
from .strategy import generate_signals
from .strategy_search import run_edge_strategy_search
from .strategy_v2_evidence import build_strategy_v2_forward_evidence
from .strategy_v2_round_trip import build_strategy_v2_round_trip_evidence
from .validation import validate_model
from .validation_report import build_validation_report
from .websocket_collector import collect_websocket
from .websocket_normaliser import normalize_websocket_file
from .websocket_resolution_collector import collect_websocket_resolutions


COMMANDS = [
    "config-check",
    "pipeline-inventory",
    "pipeline-health",
    "inventory",
    "data-quality",
    "readiness",
    "collect-resolutions",
    "backfill-resolved-markets",
    "collect-price-history",
    "build-labels",
    "build-features",
    "build-features-v2",
    "refresh-live-features",
    "external-signals",
    "collect-external-feeds",
    "train",
    "train-calibration",
    "calibrate-categories",
    "train-skill-model",
    "optimize-model",
    "train-mispricing-alpha",
    "score-mispricing-alpha",
    "fetch-sharp-odds",
    "build-sharp-anchor",
    "refresh-sharp-anchor",
    "build-crypto-targets",
    "build-crypto-fundamental",
    "build-crypto-updown-labels",
    "edge-strategy-search",
    "anchored-edge",
    "strategy-v2-evidence",
    "strategy-v2-round-trip",
    "refresh-governance",
    "promotion-review",
    "goal-plan",
    "profit-sprint",
    "price-action-feedback",
    "price-action-microstructure",
    "price-action-model",
    "price-action-scout",
    "price-action-paper-signals",
    "closing-line-value",
    "algo-replay",
    "algo-sweep",
    "edge-attribution",
    "family-calibration",
    "evidence-history",
    "active-window-plan",
    "collection-coverage",
    "promotion-gate",
    "validation-report",
    "validate",
    "predict",
    "generate-signals",
    "backtest",
    "paper-trade",
    "simulate-paper-edge",
    "paper-readiness",
    "run-paper",
    "collect-websocket",
    "normalize-websocket",
    "scan-mispricing",
    "run-live-mispricing",
    "market-making-pnl",
    "dutch-arb-monitor",
    "longshot-bias-scan",
    "resolve-websocket-markets",
    "collect-snapshot-labels",
    "collect-overnight",
    "collection-only",
    "ingest-scanner-snapshot",
    "paper-cycle",
    "portfolio",
    "reconciliation-report",
    "governance-report",
    "live-trade",
    "init-db",
]


def _print(payload):
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polymarket-engine")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--allow-data-quality-warnings", action="store_true")
    parser.add_argument("--resolution-limit", type=int, default=None)
    parser.add_argument("--historical-limit", type=int, default=None)
    parser.add_argument(
        "--allow-old-history",
        action="store_true",
        help="Explicit research opt-in for historical markets older than the configured cutoff.",
    )
    parser.add_argument("--websocket-seconds", type=int, default=60)
    parser.add_argument("--websocket-input", default=None)
    parser.add_argument(
        "--strategy",
        default=None,
        help="algo-replay/algo-sweep: registered algo strategy name (defaults: replay=null, sweep=tight_spread_join_bid_shadow)",
    )
    parser.add_argument("--snapshot-input", default=None)
    parser.add_argument("--sharp-input", default=None, help="sharp-odds CSV for build-sharp-anchor (overrides config sharp_anchor.input_path)")
    parser.add_argument("--crypto-targets", default=None, help="crypto targets CSV for build-crypto-fundamental (token_id,currency,strike,expiry)")
    parser.add_argument("--max-labels", type=int, default=250, help="build-crypto-updown-labels: max candidate rows to inspect")
    parser.add_argument(
        "--source",
        choices=["all", "historical", "raw_snapshot", "websocket"],
        default="all",
        help="Feature source for build-features-v2. Use websocket for a fast WebSocket-only refresh.",
    )
    parser.add_argument("--test-fraction", type=float, default=0.3, help="held-out market fraction for train-skill-model")
    parser.add_argument("--skill-l2", type=float, default=5.0, help="L2 strength for train-skill-model")
    parser.add_argument("--polls", type=int, default=1, help="dutch-arb-monitor: number of book polls (0 = run continuously until stopped)")
    parser.add_argument("--poll-seconds", type=int, default=30, help="dutch-arb-monitor: seconds between polls")
    parser.add_argument("--max-events", type=int, default=20, help="dutch-arb-monitor: max events to scan per poll")
    parser.add_argument("--min-annualised", type=float, default=0.0, help="dutch-arb-monitor: min annualised ROI to rank")
    parser.add_argument("--alert-annualised", type=float, default=0.10, help="dutch-arb-monitor: alert threshold")
    parser.add_argument(
        "--allow-unlabelled-research-features",
        "--research-unlabelled-features",
        dest="research_unlabelled_features",
        action="store_true",
        help="Permit an unlabeled build-features-v2 research export. Never use it as a training manifest.",
    )
    parser.add_argument(
        "--paper-source",
        choices=["raw_snapshot", "websocket"],
        default="raw_snapshot",
        help="Point-in-time source used by the canonical forward paper cycle.",
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help="refresh-governance: do not rewrite dashboard_data.json/index.html.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config) if args.command != "config-check" else None
        if args.command == "config-check":
            _print(config_check(args.config))
        elif args.command == "pipeline-inventory":
            _print({"rows": len(pipeline_inventory(cfg))})
        elif args.command == "pipeline-health":
            rows, summary = pipeline_health(cfg)
            _print({"rows": len(rows), "summary": summary})
        elif args.command == "inventory":
            _print({"rows": len(inventory(cfg))})
        elif args.command == "data-quality":
            _, summary = data_quality(cfg, allow_warnings=args.allow_data_quality_warnings)
            _print(summary)
        elif args.command == "readiness":
            _print(readiness_decision(cfg))
        elif args.command == "collect-resolutions":
            _, _, summary = collect_resolutions(cfg, limit=args.resolution_limit)
            _print(summary)
        elif args.command == "backfill-resolved-markets":
            _, _, summary = historical_backfill(
                cfg,
                historical_limit=args.historical_limit,
                allow_old_history=args.allow_old_history,
            )
            _print(summary)
        elif args.command == "collect-price-history":
            _, _, summary = collect_price_history(cfg, historical_limit=args.historical_limit)
            _print(summary)
        elif args.command == "build-labels":
            _print({"labels": len(build_labels(cfg))})
        elif args.command == "build-features":
            _print({"features": len(build_features(cfg))})
        elif args.command == "build-features-v2":
            features = build_features_v2(
                cfg,
                source=args.source,
                allow_unlabelled_research=args.research_unlabelled_features,
                require_clean_labels=not args.research_unlabelled_features,
            )
            _print({"features_v2": len(features), "source": args.source})
        elif args.command == "refresh-live-features":
            _, _, websocket_summary = normalize_websocket_file(cfg, input_path=args.websocket_input)
            features = build_features_v2(
                cfg,
                source="websocket",
                allow_unlabelled_research=True,
                require_clean_labels=False,
            )
            _print({"status": "ok", "websocket": websocket_summary, "features_v2": len(features), "source": "websocket"})
        elif args.command == "external-signals":
            rows, quality = normalize_external_signals(cfg)
            _print({"signals": len(rows), "quality_rows": len(quality)})
        elif args.command == "collect-external-feeds":
            _print(collect_external_feeds(cfg))
        elif args.command == "train":
            out = cfg.output_root / "polymarket_models"
            out.mkdir(parents=True, exist_ok=True)
            _print(train_model(str(cfg.output_root / "polymarket_training" / "features.csv"), str(cfg.output_root / "polymarket_training" / "labels.csv"), str(out), int(cfg.raw.get("governance_thresholds", {}).get("min_training_rows", 100))))
        elif args.command == "train-calibration":
            _print(train_calibration_model(cfg))
        elif args.command == "calibrate-categories":
            _print(train_category_calibration(cfg))
        elif args.command == "train-skill-model":
            _print(train_skill_model(cfg, test_fraction=args.test_fraction, l2=args.skill_l2))
        elif args.command == "optimize-model":
            _print(train_optimized_model(cfg))
        elif args.command == "train-mispricing-alpha":
            _print(train_mispricing_alpha_model(cfg))
        elif args.command == "score-mispricing-alpha":
            _print({"predictions": len(apply_mispricing_alpha(cfg, output_path=str(cfg.output_root / "polymarket_predictions" / "predictions.csv")))})
        elif args.command == "fetch-sharp-odds":
            _print(fetch_sharp_odds(cfg))
        elif args.command == "build-sharp-anchor":
            _print(build_sharp_anchor(cfg, input_path=args.sharp_input))
        elif args.command == "refresh-sharp-anchor":
            fetched = fetch_sharp_odds(cfg)
            built = build_sharp_anchor(cfg)
            _print({"fetch": fetched, "build": built})
        elif args.command == "build-crypto-targets":
            _print(build_crypto_targets(cfg))
        elif args.command == "build-crypto-fundamental":
            _print(build_crypto_fundamental(cfg, targets_path=args.crypto_targets))
        elif args.command == "build-crypto-updown-labels":
            _print(build_crypto_updown_proxy_labels(cfg, max_rows=args.max_labels))
        elif args.command == "edge-strategy-search":
            _print(run_edge_strategy_search(cfg))
        elif args.command == "anchored-edge":
            _print(run_anchored_edge(args.config))
        elif args.command == "strategy-v2-evidence":
            _print(build_strategy_v2_forward_evidence(cfg))
        elif args.command == "strategy-v2-round-trip":
            _print(build_strategy_v2_round_trip_evidence(cfg))
        elif args.command == "refresh-governance":
            _print(refresh_governance(cfg, refresh_dashboard=not args.skip_dashboard))
        elif args.command == "promotion-review":
            _print(build_promotion_review(cfg))
        elif args.command == "goal-plan":
            _print(build_goal_plan(cfg))
        elif args.command == "profit-sprint":
            _print(build_profit_sprint(cfg))
        elif args.command == "price-action-feedback":
            _print(build_price_action_feedback(cfg))
        elif args.command == "price-action-microstructure":
            _print(build_microstructure_edge_lab(cfg))
        elif args.command == "price-action-model":
            _print(train_price_action_model(cfg))
        elif args.command == "price-action-scout":
            _print(build_price_action_scout(cfg))
        elif args.command == "price-action-paper-signals":
            _print(build_price_action_paper_signals(cfg))
        elif args.command == "closing-line-value":
            _print(build_closing_line_value(cfg, features_input=args.websocket_input))
        elif args.command == "algo-replay":
            _print(run_replay(cfg, args.strategy or "null", features_input=args.websocket_input))
        elif args.command == "algo-sweep":
            _print(run_algo_sweep(cfg, strategy_name=args.strategy or "tight_spread_join_bid_shadow", features_input=args.websocket_input))
        elif args.command == "edge-attribution":
            _print(build_edge_attribution(cfg))
        elif args.command == "family-calibration":
            _print(build_family_calibration_scorecard(cfg))
        elif args.command == "evidence-history":
            _print(append_evidence_history(cfg))
        elif args.command == "active-window-plan":
            _print(build_active_window_plan(cfg))
        elif args.command == "collection-coverage":
            _print(build_collection_coverage(cfg))
        elif args.command == "promotion-gate":
            _print(paper_live_promotion_gate(cfg))
        elif args.command == "validation-report":
            _print(build_validation_report(cfg))
        elif args.command == "validate":
            _print(validate_model(cfg))
        elif args.command == "predict":
            lock_settings = cfg.raw.get("runtime_resource_guard", {}) or {}
            lock_stale_seconds = float(lock_settings.get("prediction_cycle_lock_stale_seconds", 1800) or 1800)
            with runtime_lock(cfg, "prediction_cycle", stale_after_seconds=lock_stale_seconds) as lock:
                if not lock.acquired:
                    _print(
                        {
                            "status": "skipped_existing_prediction_cycle",
                            "source": args.source,
                            "runtime_lock": lock.as_dict(),
                            "paper_trading_invoked": False,
                            "live_trading_invoked": False,
                        }
                    )
                else:
                    features = build_features_v2(cfg, source=args.source, require_clean_labels=False)
                    global_model, category_models = load_prediction_models(cfg)
                    out = cfg.output_root / "polymarket_predictions"
                    out.mkdir(parents=True, exist_ok=True)
                    _print(
                        {
                            "predictions": len(
                                write_predictions(
                                    features,
                                    str(out / "predictions.csv"),
                                    model=global_model,
                                    category_models=category_models,
                                    training_cutoff=str(global_model.get("trained_at", "")),
                                )
                            )
                        }
                    )
        elif args.command == "generate-signals":
            approved, rejected = generate_signals(cfg)
            _print({"approved": len(approved), "rejected": len(rejected)})
        elif args.command == "backtest":
            _print(backtest(cfg))
        elif args.command == "paper-trade":
            _print(paper_trade_report(cfg))
        elif args.command == "simulate-paper-edge":
            _print(simulate_paper_edge(cfg))
        elif args.command == "paper-readiness":
            _print(paper_trade_readiness(cfg))
        elif args.command == "run-paper":
            _print(run_paper_session(cfg))
        elif args.command == "collect-websocket":
            _print(collect_websocket(cfg, websocket_seconds=args.websocket_seconds))
        elif args.command == "normalize-websocket":
            _, _, summary = normalize_websocket_file(cfg, input_path=args.websocket_input)
            _print(summary)
        elif args.command == "scan-mispricing":
            _, summary = scan_live_mispricing(cfg)
            _print(summary)
        elif args.command == "run-live-mispricing":
            _print(run_live_mispricing(cfg, websocket_seconds=args.websocket_seconds))
        elif args.command == "market-making-pnl":
            _, summary = evaluate_market_making(cfg)
            _print(summary)
        elif args.command == "dutch-arb-monitor":
            _print(run_dutch_arb_monitor(cfg, polls=args.polls, poll_seconds=args.poll_seconds,
                                         max_events=args.max_events, min_annualised=args.min_annualised,
                                         alert_annualised=args.alert_annualised))
        elif args.command == "longshot-bias-scan":
            _print(build_longshot_bias_scan(cfg))
        elif args.command == "resolve-websocket-markets":
            _print(collect_websocket_resolutions(cfg))
        elif args.command == "collect-snapshot-labels":
            _print(collect_snapshot_labels(cfg))
        elif args.command == "collect-overnight":
            _print(
                run_collection_only_overnight(
                    cfg,
                    websocket_seconds=args.websocket_seconds,
                    websocket_input=args.websocket_input,
                )
            )
        elif args.command == "collection-only":
            _print(run_collection_only(cfg, websocket_seconds=args.websocket_seconds))
        elif args.command == "ingest-scanner-snapshot":
            _print(ingest_scanner_snapshot(cfg, snapshot_path=args.snapshot_input))
        elif args.command == "paper-cycle":
            _print(run_paper_cycle(cfg, source=args.paper_source))
        elif args.command == "portfolio":
            _print(portfolio_snapshot(cfg))
        elif args.command == "reconciliation-report":
            _print(reconciliation_report(cfg))
        elif args.command == "governance-report":
            _print(governance_report(cfg))
        elif args.command == "live-trade":
            _print(live_trade(cfg))
        elif args.command == "init-db":
            init_db(cfg.database_path)
            _print({"database_path": str(cfg.database_path), "status": "ok"})
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
