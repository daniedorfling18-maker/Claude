#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

out_dir = Path(os.getenv("POLYMARKET_OUTPUT_DIR", "outputs/polymarket"))
snapshot = out_dir / "market_snapshot.csv"
intents = out_dir / "long_short_intents.csv"
poll = int(os.getenv("MM_EVAL_POLL_SECONDS", "10"))

print(f"market-making evaluator python loop: watching {intents} and {snapshot} (poll {poll}s)", flush=True)

last = None
while True:
    try:
        if snapshot.exists() and intents.exists():
            current = (snapshot.stat().st_mtime, intents.stat().st_mtime)
            if current != last:
                subprocess.run(
                    [
                        sys.executable,
                        "scripts/polymarket_market_making_eval.py",
                        "--snapshot",
                        str(snapshot),
                        "--intents",
                        str(intents),
                        "--state",
                        str(out_dir / "mm_quote_state.csv"),
                        "--eval-csv",
                        str(out_dir / "mm_quote_evaluations.csv"),
                        "--summary-csv",
                        str(out_dir / "mm_quote_summary.csv"),
                    ],
                    check=False,
                )
                last = current
    except Exception as exc:
        print(f"market-making evaluator python loop error continuing: {exc}", flush=True)
    time.sleep(poll)