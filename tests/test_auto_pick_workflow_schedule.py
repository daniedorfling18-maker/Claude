from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto_pick.yml"
VPS_COMPOSE = ROOT / "docker-compose.vps-paper.yml"
VPS_WATCHDOG = ROOT / "scripts" / "run_superbru_auto_pick_watchdog.sh"


def test_auto_pick_github_schedule_is_a_thin_safety_net_not_a_watchdog() -> None:
    """2026-07-08: a 15-minute GitHub cron burned ~250-330 billed Actions
    minutes/day, exhausted the monthly quota, and GitHub then BLOCKED all
    scheduled runs. The VPS watchdog container is the primary 15-minute lane
    (free, on our own server); the GitHub schedule must stay at a handful of
    daily safety runs at most."""
    text = WORKFLOW.read_text(encoding="utf-8")
    crons = re.findall(r"cron:\s*'([^']+)'", text)

    assert crons, "auto_pick must keep a safety-net schedule"
    runs_per_day = 0
    for cron in crons:
        minute_field, hour_field = cron.split()[:2]
        minutes = len(minute_field.split(","))
        hours = 0
        for token in hour_field.split(","):
            if "-" in token:
                start, end = token.split("-")
                hours += int(end) - int(start) + 1
            elif token == "*":
                hours += 24
            else:
                hours += 1
        runs_per_day += minutes * hours
    assert runs_per_day <= 4, f"GitHub auto-pick schedule too hot: ~{runs_per_day} runs/day burns paid Actions minutes"

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
    assert "--revision-window-minutes \"$REVISION_WINDOW_MINUTES\"" in workflow
    assert "SUPERBRU_AUTO_PICK_REVISION_WINDOW_MINUTES:-260" in compose
    assert "SUPERBRU_AUTO_PICK_REVISION_WINDOW_MINUTES:-260" in watchdog


def test_auto_pick_watchdog_reports_confirmed_picks_not_only_submissions() -> None:
    watchdog = VPS_WATCHDOG.read_text(encoding="utf-8")

    assert "confirmed_picks_count" in watchdog
    assert "submitted_picks_count" in watchdog
    assert "already_current_picks_count" in watchdog
    assert "already present on SuperBru" in watchdog
