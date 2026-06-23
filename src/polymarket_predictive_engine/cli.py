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
from .external_signals import normalize_external_signals
from .features import build_features
from .governance import governance_report
from .labels import build_labels
from .models.calibrated import train_model, write_predictions
from .pipeline_health import pipeline_health
from .pipeline_inventory import pipeline_inventory
from .resolution_collector import collect_resolutions
from .portfolio import portfolio_snapshot
from .readiness import readiness_decision
from .storage import init_db
from .strategy import generate_signals
from .validation import validate_model


def _print(payload):
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polymarket-engine")
    parser.add_argument("command", choices=["config-check", "pipeline-inventory", "pipeline-health", "inventory", "data-quality", "readiness", "collect-resolutions", "build-labels", "build-features", "external-signals", "train", "validate", "predict", "generate-signals", "backtest", "paper-trade", "portfolio", "governance-report", "live-trade", "init-db"])
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--allow-data-quality-warnings", action="store_true")
    parser.add_argument("--resolution-limit", type=int, default=None)
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
            rows, summary = pipeline_health(cfg); _print({"rows": len(rows), "summary": summary})
        elif args.command == "inventory":
            _print({"rows": len(inventory(cfg))})
        elif args.command == "data-quality":
            issues, summary = data_quality(cfg, allow_warnings=args.allow_data_quality_warnings); _print(summary)
        elif args.command == "readiness":
            _print(readiness_decision(cfg))
        elif args.command == "collect-resolutions":
            _, _, summary = collect_resolutions(cfg, limit=args.resolution_limit); _print(summary)
        elif args.command == "build-labels":
            _print({"labels": len(build_labels(cfg))})
        elif args.command == "build-features":
            _print({"features": len(build_features(cfg))})
        elif args.command == "external-signals":
            rows, quality = normalize_external_signals(cfg); _print({"signals": len(rows), "quality_rows": len(quality)})
        elif args.command == "train":
            out = cfg.output_root / "polymarket_models"; out.mkdir(parents=True, exist_ok=True)
            _print(train_model(str(cfg.output_root / "polymarket_training" / "features.csv"), str(cfg.output_root / "polymarket_training" / "labels.csv"), str(out), int(cfg.raw.get("governance_thresholds", {}).get("min_training_rows", 100))))
        elif args.command == "validate":
            _print(validate_model(cfg))
        elif args.command == "predict":
            features = build_features(cfg)
            out = cfg.output_root / "polymarket_predictions"; out.mkdir(parents=True, exist_ok=True)
            _print({"predictions": len(write_predictions(features, str(out / "predictions.csv")))})
        elif args.command == "generate-signals":
            approved, rejected = generate_signals(cfg); _print({"approved": len(approved), "rejected": len(rejected)})
        elif args.command == "backtest":
            _print(backtest(cfg))
        elif args.command == "paper-trade":
            _print(paper_trade(cfg))
        elif args.command == "portfolio":
            _print(portfolio_snapshot(cfg))
        elif args.command == "governance-report":
            _print(governance_report(cfg))
        elif args.command == "live-trade":
            _print(live_trade(cfg))
        elif args.command == "init-db":
            init_db(cfg.database_path); _print({"database_path": str(cfg.database_path), "status": "ok"})
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
