from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto_pick.yml"
VPS_COMPOSE = ROOT / "docker-compose.vps-paper.yml"
VPS_WATCHDOG = ROOT / "scripts" / "run_superbru_auto_pick_watchdog.sh"


def test_auto_pick_watchdog_cron_uses_github_safe_minute_tokens() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    crons = re.findall(r"cron:\s*'([^']+)'", text)

    assert "7,22,37,52 14-23 4-19 7 *" in crons
    assert "7,22,37,52 0-3 5-20 7 *" in crons

    minute_fields = [cron.split()[0] for cron in crons]
    assert all(not token.startswith("0") or token == "0" for field in minute_fields for token in field.split(","))


def test_auto_pick_uses_broad_early_card_window_by_default() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    compose = VPS_COMPOSE.read_text(encoding="utf-8")
    watchdog = VPS_WATCHDOG.read_text(encoding="utf-8")

    assert "default: '5000'" in workflow
    assert "github.event.inputs.window_minutes || '5000'" in workflow
    assert "SUPERBRU_AUTO_PICK_WINDOW_MINUTES:-5000" in compose
    assert "SUPERBRU_AUTO_PICK_WINDOW_MINUTES:-5000" in watchdog
