from __future__ import annotations

import re
import subprocess
import sys

TIMEOUT_SECONDS = 45

collect = subprocess.run(
    [sys.executable, "-m", "pytest", "tests", "--collect-only", "-vv", "--color=no"],
    text=True,
    capture_output=True,
)

print(collect.stdout)
print(collect.stderr, file=sys.stderr)

nodes = []
for line in collect.stdout.splitlines():
    line = line.strip()
    match = re.search(r"(tests[\\/].*?::test[^\s<]+)", line)
    if match:
        nodes.append(match.group(1).replace("\\", "/"))

nodes = sorted(set(nodes))

print(f"\nCollected node ids: {len(nodes)}")

if not nodes:
    raise SystemExit("No test node IDs found. Paste the collect output above.")

for i, node in enumerate(nodes, start=1):
    print(f"\n[{i}/{len(nodes)}] {node}", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "-s", "--color=no"],
            timeout=TIMEOUT_SECONDS,
            text=True,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        print(f"\nTIMEOUT after {TIMEOUT_SECONDS}s:")
        print(node)
        raise SystemExit(124)

    if result.returncode != 0:
        print("\nFAILED:")
        print(node)
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)

print("\nAll node-level tests passed within timeout.")
