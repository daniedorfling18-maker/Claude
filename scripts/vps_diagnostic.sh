#!/usr/bin/env sh
# One-shot VPS diagnostic: host, docker, per-container, and app-job state in
# a single size-capped report. Written to outputs/ops_scheduler/ with a .log
# name deliberately, so the telemetry bridge ships it on its next push and
# the remote orchestrator can analyse it without any terminal paste.
#
# Run [VPS]:
#   sudo sh scripts/vps_diagnostic.sh
#   sh scripts/push_vps_telemetry.sh    # optional: ship it immediately
#
# Every section is fail-soft: a missing tool prints a note instead of
# aborting, so the report is always produced.
set -u

OUT="${VPS_DIAG_OUT:-outputs/ops_scheduler/vps_diagnostic.log}"
COMPOSE="${VPS_DIAG_COMPOSE:-docker-compose.vps-paper.yml}"
LOG_TAIL="${VPS_DIAG_LOG_TAIL:-40}"
REPO_USER="${VPS_DIAG_REPO_USER:-opc}"

mkdir -p "$(dirname "$OUT")" 2>/dev/null || true

sec() {
  echo
  echo "==================== $1 ===================="
}

{
  echo "VPS DIAGNOSTIC $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname 2>/dev/null || echo unknown)"

  sec "HOST: kernel / uptime / load"
  uname -a 2>/dev/null || true
  uptime 2>/dev/null || true

  sec "HOST: memory (MB)"
  free -m 2>/dev/null || echo "free unavailable"

  sec "HOST: disk"
  df -h / 2>/dev/null || true
  echo "-- largest outputs dirs --"
  du -sh outputs/* 2>/dev/null | sort -rh | head -12 || true

  sec "HOST: top 12 processes by CPU"
  ps aux --sort=-%cpu 2>/dev/null | head -13 || ps aux | head -13

  sec "HOST: top 12 processes by memory"
  ps aux --sort=-%mem 2>/dev/null | head -13 || true

  sec "HOST: failed systemd units"
  systemctl --failed --no-pager 2>/dev/null | head -12 || echo "systemctl unavailable"

  sec "HOST: journal errors, last 24h (tail 25)"
  journalctl -p err --since "-24h" --no-pager 2>/dev/null | tail -25 || echo "journalctl unavailable"

  sec "HOST: listening sockets"
  ss -tlnp 2>/dev/null | head -20 || netstat -tlnp 2>/dev/null | head -20 || echo "ss/netstat unavailable"

  sec "REPO: git state"
  git rev-parse --short HEAD 2>/dev/null || true
  git rev-parse --abbrev-ref HEAD 2>/dev/null || true
  echo "dirty files: $(git status --porcelain 2>/dev/null | wc -l)"

  sec "CRON: telemetry + user jobs"
  crontab -l -u "$REPO_USER" 2>/dev/null || crontab -l 2>/dev/null || echo "no crontab readable"
  echo "-- last telemetry pushes --"
  tail -5 "/home/$REPO_USER/vps_telemetry_push.log" 2>/dev/null || echo "no telemetry push log"

  sec "DOCKER: system df"
  docker system df 2>/dev/null || echo "docker unavailable (run with sudo)"

  sec "DOCKER: containers (status, restarts, health)"
  docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}' 2>/dev/null || true
  for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
    echo "-- $c: restarts=$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null) health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null) oom=$(docker inspect -f '{{.State.OOMKilled}}' "$c" 2>/dev/null)"
  done

  sec "DOCKER: live resource usage"
  docker stats --no-stream 2>/dev/null || true

  for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
    sec "CONTAINER $c: processes"
    docker top "$c" 2>/dev/null | head -15 || true
    sec "CONTAINER $c: log tail ($LOG_TAIL)"
    docker logs --tail "$LOG_TAIL" "$c" 2>&1 | tail -"$LOG_TAIL" || true
  done

  sec "APP: scheduler job status"
  cat outputs/ops_scheduler/status.json 2>/dev/null || echo "no status.json"

  sec "APP: scheduler log tail (30)"
  tail -30 outputs/ops_scheduler/ops_scheduler.log 2>/dev/null || echo "no scheduler log"

  sec "APP: freshest decision artifacts"
  ls -lat outputs/maker_carry/ 2>/dev/null | head -12 || true
  ls -lat outputs/polymarket_model_governance/ 2>/dev/null | head -8 || true

  echo
  echo "==================== END DIAGNOSTIC ===================="
} > "$OUT" 2>&1

SIZE_KB=$(du -k "$OUT" 2>/dev/null | cut -f1)
# Stay comfortably under the telemetry bridge's 300KB per-file cap.
if [ "${SIZE_KB:-0}" -gt 280 ]; then
  tail -c 280000 "$OUT" > "$OUT.trim" && mv "$OUT.trim" "$OUT"
  SIZE_KB=280
fi
echo "diagnostic written to $OUT (${SIZE_KB:-?}KB); it rides the next telemetry push"
