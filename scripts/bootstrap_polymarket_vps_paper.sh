#!/usr/bin/env sh
# Bootstrap the lean Polymarket paper stack on an Ubuntu VPS.
#
# Safe defaults:
# - paper/dry-run only
# - no private keys required
# - one lean compose stack
# - bounded memory/asset defaults based on VPS RAM

set -eu

REPO_URL="${PM_VPS_REPO_URL:-https://github.com/daniedorfling18-maker/Claude.git}"
REPO_BRANCH="${PM_VPS_REPO_BRANCH:-main}"
REPO_DIR="${PM_VPS_REPO_DIR:-$HOME/Claude}"
COMPOSE_FILE="${PM_VPS_COMPOSE_FILE:-docker-compose.vps-paper.yml}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  printf '%s\n' "[polymarket-vps] $*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

install_base_packages() {
  log "Installing base packages"
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl git
}

install_docker_if_needed() {
  if need_cmd docker && docker --version >/dev/null 2>&1; then
    log "Docker already installed"
  else
    log "Installing Docker from Ubuntu packages"
    if ! $SUDO apt-get install -y docker.io docker-compose-plugin; then
      log "Ubuntu package install did not provide Docker; falling back to Docker's official install script"
      curl -fsSL https://get.docker.com | $SUDO sh
    fi
  fi

  if ! docker compose version >/dev/null 2>&1; then
    if ! $SUDO docker compose version >/dev/null 2>&1; then
      log "Docker Compose plugin is missing. Install docker-compose-plugin, then rerun this script."
      exit 1
    fi
  fi

  if [ -n "$SUDO" ]; then
    $SUDO usermod -aG docker "$USER" || true
    log "Added $USER to docker group where possible. This run will use sudo if the group is not active yet."
  fi
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    $SUDO docker "$@"
  fi
}

clone_or_update_repo() {
  if [ -d "$REPO_DIR/.git" ]; then
    log "Updating repo at $REPO_DIR"
    git -C "$REPO_DIR" fetch origin "$REPO_BRANCH"
    git -C "$REPO_DIR" checkout "$REPO_BRANCH"
    git -C "$REPO_DIR" pull --ff-only origin "$REPO_BRANCH"
  else
    log "Cloning repo to $REPO_DIR"
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
  fi
}

set_env_value() {
  key="$1"
  value="$2"
  file="$3"
  if grep -q "^${key}=" "$file"; then
    tmp="${file}.tmp.$$"
    sed "s|^${key}=.*|${key}=${value}|" "$file" > "$tmp"
    mv "$tmp" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

configure_env_if_new() {
  cd "$REPO_DIR"
  if [ -f .env ]; then
    log ".env already exists; leaving your current settings in place"
    return
  fi

  cp .env.vps-paper.example .env
  total_mb="$(awk '/MemTotal/ {printf "%.0f", $2 / 1024}' /proc/meminfo 2>/dev/null || printf '0')"
  if [ "${total_mb:-0}" -gt 0 ] && [ "$total_mb" -le 5000 ]; then
    log "Detected a small VPS (${total_mb} MB RAM); applying leaner paper settings"
    set_env_value PM_PAPER_MEM_LIMIT 2g .env
    set_env_value POLYMARKET_WEBSOCKET_MAX_ASSETS 80 .env
    set_env_value POLYMARKET_EVENT_LIMIT 100 .env
  else
    log "Using default VPS paper settings"
  fi
}

start_stack() {
  cd "$REPO_DIR"
  mkdir -p outputs work inputs/polymarket
  log "Starting Docker stack: $COMPOSE_FILE"
  docker_cmd compose -f "$COMPOSE_FILE" up -d --build
}

wait_for_dashboard() {
  cd "$REPO_DIR"
  port="$(grep -E '^POLYMARKET_DASHBOARD_PORT=' .env | tail -n 1 | cut -d= -f2-)"
  port="${port:-8765}"
  url="http://127.0.0.1:${port}/"
  log "Waiting for dashboard at $url"
  i=0
  while [ "$i" -lt 60 ]; do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      log "Dashboard is responding locally: $url"
      public_ip="$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)"
      if [ -n "$public_ip" ]; then
        log "If your VPS firewall allows it, open: http://${public_ip}:${port}/"
      else
        log "Public dashboard URL is http://<VPS_PUBLIC_IP>:${port}/ once the firewall allows it"
      fi
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  log "Dashboard did not respond within the wait window; showing compose status"
  docker_cmd compose -f "$COMPOSE_FILE" ps
  return 1
}

main() {
  install_base_packages
  install_docker_if_needed
  clone_or_update_repo
  configure_env_if_new
  start_stack
  wait_for_dashboard || true
  log "Run this anytime for status: bash $REPO_DIR/scripts/check_polymarket_vps_paper.sh"
}

main "$@"
