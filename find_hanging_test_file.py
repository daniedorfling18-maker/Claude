from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 120

files = sorted(Path("tests").rglob("test_*.py"))

print(f"Collected {len(files)} test files")

for i, path in enumerate(files, start=1):
    print(f"\n[{i}/{len(files)}] {path}", flush=True)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "-q", "-s", "--color=no"],
            timeout=TIMEOUT_SECONDS,
            text=True,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        print(f"\nTIMEOUT after {TIMEOUT_SECONDS}s:")
        print(path)
        raise SystemExit(124)

    if result.returncode != 0:
        print("\nFAILED:")
        print(path)
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)

print("\nAll test files passed within timeout.")
