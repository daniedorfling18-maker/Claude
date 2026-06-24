from __future__ import annotations

import argparse
import json
import sys

from .backtest import backtest
from .config import config_check, load_config
from .data_inventory import inventory
from .data_quality import data_quality
from .execution.live import live_trade
from .execution.paper import paper_trade
from .external_feed_collector import collect_external_feeds
from .external_signals import normalize_external_signals
from .features import build_features
from .features_v2 import build_features_v2
from .governance import governance_report
from .historical_backfill import historical_backfill
from .labels import build_labels
from .models.calibrated import train_model, write_predictions
from .models.calibration_v2 import train_calibration_model
from .models.category_calibration import train_category_calibration
from .paper_edge_simulator import simulate_paper_edge
from .pipeline_health import pipeline_health
from .pipeline_inventory import pipeline_inventory
from .portfolio import portfolio_snapshot
from .price_history_collector import collect_price_history
from .readiness import readiness_decision
from .resolution_collector import collect_resolutions
from .snapshot_label_collector import collect_snapshot_labels
from .storage import init_db
from .strategy import generate_signals
from .validation import validate_model
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
    "validate",
    "predict",
    "generate-signals",
    "backtest",
    "paper-trade",
    "simulate-paper-edge",
    "collect-websocket",
    "normalize-websocket",
    "resolve-websocket-markets",
    "collect-snapshot-labels",
    "portfolio",
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
    parser.add_argument("--websocket-seconds", type=int, default=60)
    parser.add_argument("--websocket-input", default=None)
    parser.add_argument(
        "--source",
        choices=["all", "historical", "raw_snapshot", "websocket"],
        default="all",
        help="Feature source for build-features-v2. Use websocket for a fast WebSocket-only refresh.",
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
            _, _, summary = historical_backfill(cfg, historical_limit=args.historical_limit)
            _print(summary)
        elif args.command == "collect-price-history":
            _, _, summary = collect_price_history(cfg, historical_limit=args.historical_limit)
            _print(summary)
        elif args.command == "build-labels":
            _print({"labels": len(build_labels(cfg))})
        elif args.command == "build-features":
            _print({"features": len(build_features(cfg))})
        elif args.command == "build-features-v2":
            _print({"features_v2": len(build_features_v2(cfg, source=args.source)), "source": args.source})
        elif args.command == "refresh-live-features":
            _, _, websocket_summary = normalize_websocket_file(cfg, input_path=args.websocket_input)
            features = build_features_v2(cfg, source="websocket")
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
        elif args.command == "validate":
            _print(validate_model(cfg))
        elif args.command == "predict":
            features = build_features(cfg)
            out = cfg.output_root / "polymarket_predictions"
            out.mkdir(parents=True, exist_ok=True)
            _print({"predictions": len(write_predictions(features, str(out / "predictions.csv")))})
        elif args.command == "generate-signals":
            approved, rejected = generate_signals(cfg)
            _print({"approved": len(approved), "rejected": len(rejected)})
        elif args.command == "backtest":
            _print(backtest(cfg))
        elif args.command == "paper-trade":
            _print(paper_trade(cfg))
        elif args.command == "simulate-paper-edge":
            _print(simulate_paper_edge(cfg))
        elif args.command == "collect-websocket":
            _print(collect_websocket(cfg, websocket_seconds=args.websocket_seconds))
        elif args.command == "normalize-websocket":
            _, _, summary = normalize_websocket_file(cfg, input_path=args.websocket_input)
            _print(summary)
        elif args.command == "resolve-websocket-markets":
            _print(collect_websocket_resolutions(cfg))
        elif args.command == "collect-snapshot-labels":
            _print(collect_snapshot_labels(cfg))
        elif args.command == "portfolio":
            _print(portfolio_snapshot(cfg))
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
