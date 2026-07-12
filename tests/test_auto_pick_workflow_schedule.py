from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto_pick.yml"
VPS_COMPOSE = ROOT / "docker-compose.vps-paper.yml"
VPS_WATCHDOG = ROOT / "scripts" / "run_superbru_auto_pick_watchdog.sh"


def test_auto_pick_has_no_github_schedule_actions_is_dispatch_only() -> None:
    """2026-07-09: the Actions minutes quota was exhausted, so ALL recurring
    jobs moved to the VPS ops scheduler and operational workflows became
    dispatch-only. WO-69's self-hosted PR guard is the deliberate exception;
    a reintroduced cron here would silently burn hosted minutes again."""
    text = WORKFLOW.read_text(encoding="utf-8")
    crons = re.findall(r"cron:\s*'([^']+)'", text)

    assert not crons, f"auto_pick must stay dispatch-only, found schedule: {crons}"
    assert "workflow_dispatch" in text


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
