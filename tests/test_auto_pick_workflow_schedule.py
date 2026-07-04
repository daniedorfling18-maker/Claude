from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto_pick.yml"


def test_auto_pick_watchdog_cron_uses_github_safe_minute_tokens() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    crons = re.findall(r"cron:\s*'([^']+)'", text)

    assert "5,35 14-23 4-19 7 *" in crons
    assert "5,35 0-3 5-20 7 *" in crons

    minute_fields = [cron.split()[0] for cron in crons]
    assert all(not token.startswith("0") or token == "0" for field in minute_fields for token in field.split(","))
