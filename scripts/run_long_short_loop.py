#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

out_dir = Path(os.getenv("POLYMARKET_OUTPUT_DIR", "outputs/polymarket"))
snapshot = out_dir / "market_snapshot.csv"
poll = int(os.getenv("LONG_SHORT_POLL_SECONDS", "3"))
min_edge = os.getenv("LONG_SHORT_MIN_EDGE", "0.04")
max_orders = os.getenv("LONG_SHORT_MAX_LIVE_ORDERS", "3")

print(f"long/short python loop: watching {snapshot} (poll {poll}s, min_edge={min_edge}, max_orders={max_orders})", flush=True)

last = None
while True:
    try:
        if snapshot.exists():
            current = snapshot.stat().st_mtime
            if current != last:
                subprocess.run(
                    [
                        sys.executable,
                        "scripts/polymarket_long_short_engine.py",
                        "--market-snapshot",
                        str(snapshot),
                        "--out-csv",
                        str(out_dir / "long_short_intents.csv"),
                        "--min-edge",
                        str(min_edge),
                        "--max-live-orders",
                        str(max_orders),
                    ],
                    check=False,
                )
                last = current
    except Exception as exc:
        print(f"long/short python loop error continuing: {exc}", flush=True)
    time.sleep(poll)