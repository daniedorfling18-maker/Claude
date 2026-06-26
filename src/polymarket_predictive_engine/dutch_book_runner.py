from __future__ import annotations

import argparse
import json

from .config import load_config
from .dutch_book_monitor import run_dutch_book_monitor, scan_dutch_book_arbitrage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m polymarket_predictive_engine.dutch_book_runner")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--once", action="store_true", help="Run one dry-run scan without collecting a fresh websocket window.")
    parser.add_argument("--cycles", type=int, default=None, help="Number of monitor cycles. Defaults to config value.")
    parser.add_argument("--sleep-seconds", type=int, default=None, help="Seconds to sleep between cycles.")
    parser.add_argument("--websocket-seconds", type=int, default=None, help="Seconds to collect websocket books per cycle.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.once:
        payload = scan_dutch_book_arbitrage(cfg)
    else:
        payload = run_dutch_book_monitor(
            cfg,
            cycles=args.cycles,
            sleep_seconds=args.sleep_seconds,
            websocket_seconds=args.websocket_seconds,
        )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
