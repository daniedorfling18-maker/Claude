#!/usr/bin/env sh
# Bootstrap the lean Polymarket paper stack on a Linux VPS.
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

package_manager() {
  if need_cmd apt-get; then
    printf '%s' "apt-get"
  elif need_cmd dnf; then
    printf '%s' "dnf"
  elif need_cmd yum; then
    printf '%s' "yum"
  else
    printf '%s' ""
  fi
}

install_base_packages() {
  log "Installing base packages"
  pm="$(package_manager)"
  case "$pm" in
    apt-get)
      $SUDO apt-get update
      $SUDO apt-get install -y ca-certificates curl git
      ;;
    dnf|yum)
      $SUDO "$pm" install -y ca-certificates curl git
      ;;
    *)
      log "No supported package manager found. Install git, curl, and Docker manually, then rerun."
      exit 1
      ;;
  esac
}

install_docker_if_needed() {
  if need_cmd docker && docker --version >/dev/null 2>&1; then
    log "Docker already installed"
  else
    pm="$(package_manager)"
    case "$pm" in
      apt-get)
        log "Installing Docker from Ubuntu/Debian packages"
        if ! $SUDO apt-get install -y docker.io docker-compose-plugin; then
          log "Package install did not provide Docker; falling back to Docker's official install script"
          curl -fsSL https://get.docker.com | $SUDO sh
        fi
        ;;
      dnf|yum)
        log "Installing Docker from Oracle/RHEL-family packages"
        $SUDO "$pm" install -y dnf-plugins-core yum-utils >/dev/null 2>&1 || true
        if ! $SUDO "$pm" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
          log "Adding Docker's RHEL-compatible package repository"
          if need_cmd dnf; then
            $SUDO dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
          else
            $SUDO yum-config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
          fi
          $SUDO "$pm" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        fi
        ;;
      *)
        log "No supported package manager found. Install Docker manually, then rerun."
        exit 1
        ;;
    esac
  fi

  if need_cmd systemctl; then
    $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  elif need_cmd service; then
    $SUDO service docker start >/dev/null 2>&1 || true
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
    log "Fetching target revision without changing the running checkout at $REPO_DIR"
    git -C "$REPO_DIR" fetch origin "$REPO_BRANCH"
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
  preflight_dir=$(mktemp -d)
  trap 'rm -rf "$preflight_dir"' EXIT INT TERM
  git show "origin/$REPO_BRANCH:scripts/preflight_vps_capacity.py" > "$preflight_dir/preflight_vps_capacity.py"
  git show "origin/$REPO_BRANCH:scripts/update_vps_checkout_preserving_runtime.py" > "$preflight_dir/update_vps_checkout_preserving_runtime.py"
  git show "origin/$REPO_BRANCH:$COMPOSE_FILE" > "$preflight_dir/compose.yml"
  log "Checking host capacity against the target revision before changing the running checkout"
  python3 "$preflight_dir/preflight_vps_capacity.py" \
    --compose "$preflight_dir/compose.yml" \
    --env-file .env \
    --output outputs/performance/vps_capacity_preflight.json \
    --root .

  log "Updating source while preserving VPS runtime evidence"
  python3 "$preflight_dir/update_vps_checkout_preserving_runtime.py" \
    --repo . \
    --target-ref "origin/$REPO_BRANCH" \
    --branch "$REPO_BRANCH" \
    --report outputs/performance/vps_checkout_update.json
  rm -rf "$preflight_dir"
  trap - EXIT INT TERM
  deployed_sha=$(git rev-parse HEAD)
  set_env_value PM_VPS_DEPLOYED_SHA "$deployed_sha" .env
  log "Starting Docker stack: $COMPOSE_FILE"
  docker_cmd compose -f "$COMPOSE_FILE" up -d --build
  marker_tmp="outputs/performance/deployed_git_rev.tmp.$$"
  printf '%s\n' "$deployed_sha" > "$marker_tmp"
  mv "$marker_tmp" outputs/performance/deployed_git_rev
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
      log "Remote access remains closed until authenticated tailnet HTTPS is configured."
      log "Next: install/enrol Tailscale, then run bash scripts/configure_polymarket_dashboard_tailscale.sh"
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
  # WO-122 (OPS-27): `|| true` made a bootstrap whose dashboard never came up
  # look identical to a successful one, including its exit code.
  dashboard_ready=true
  wait_for_dashboard || dashboard_ready=false
  log "Run this anytime for status: bash $REPO_DIR/scripts/check_polymarket_vps_paper.sh"
  if [ "$dashboard_ready" != true ]; then
    log "FAIL: bootstrap finished but the dashboard never responded locally; see the compose status above."
    return 1
  fi
}

main "$@"
