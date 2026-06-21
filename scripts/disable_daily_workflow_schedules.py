from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(".github/workflows")
KEEP = {"auto_pick.yml", "auto_pick.yaml"}
DAILY_MARKERS = [
    "run_daily_robust_pipeline.py",
    "run_daily_superbru",
    "daily robust",
    "Daily Superbru",
    "daily_superbru",
]


def remove_schedule_block(text: str) -> tuple[str, bool]:
    lines = text.splitlines()
    out: list[str] = []
    removed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("  schedule:"):
            removed = True
            out.append("  # schedule disabled: auto_pick.yml is the only scheduled workflow")
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("  workflow_dispatch:") or nxt.startswith("jobs:") or (nxt.startswith("  ") and not nxt.startswith("    ") and not nxt.startswith("      ")):
                    break
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out) + "\n", removed


def main() -> int:
    if not WORKFLOWS.exists():
        print("No .github/workflows directory found.")
        return 0

    changed = 0
    for path in WORKFLOWS.iterdir():
        if path.name in KEEP or path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8")
        if not any(marker.lower() in text.lower() for marker in DAILY_MARKERS):
            continue
        updated, removed = remove_schedule_block(text)
        if removed:
            path.write_text(updated, encoding="utf-8")
            changed += 1
            print(f"Disabled schedule in {path}")

    print(f"Daily workflow schedules disabled: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
