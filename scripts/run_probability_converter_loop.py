#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

out_dir = Path(os.getenv("POLYMARKET_OUTPUT_DIR", "outputs/polymarket"))
snapshot = out_dir / "market_snapshot.csv"
poll = int(os.getenv("PROBABILITY_CONVERTER_POLL_SECONDS", "5"))

print(f"probability converter python loop: watching {snapshot} (poll {poll}s)", flush=True)

last = None
while True:
    try:
        if snapshot.exists():
            current = snapshot.stat().st_mtime
            if current != last:
                subprocess.run(
                    [sys.executable, "scripts/build_repo_worldcup_winner_probabilities.py"],
                    check=False,
                )
                last = current
    except Exception as exc:
        print(f"probability converter python loop error continuing: {exc}", flush=True)
    time.sleep(poll)