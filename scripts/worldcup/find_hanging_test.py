from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 45

nodes = [
    line.strip()
    for line in Path("test_nodes.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    if "::" in line and not line.startswith("<")
]

print(f"Collected {len(nodes)} tests")

for i, node in enumerate(nodes, start=1):
    print(f"\n[{i}/{len(nodes)}] {node}", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "-s"],
            timeout=TIMEOUT_SECONDS,
            text=True,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {TIMEOUT_SECONDS}s: {node}")
        raise SystemExit(124)

    if result.returncode != 0:
        print("FAILED:", node)
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)

print("\nAll node-level tests passed within timeout.")
