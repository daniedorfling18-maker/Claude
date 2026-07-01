# Polymarket VPS Docker runbook

Use this when the laptop cannot carry the live paper system. Docker helps only
on a remote VPS or other separate machine; local Docker still consumes the
laptop's RAM.

## Recommended starting VPS

Start with the cheapest machine that gives the model room to breathe:

- Best cost path: Oracle Cloud Always Free Ampere A1, currently documented as
  up to 2 OCPUs and 12 GB RAM across Always Free A1 instances. This is ARM-based,
  but the repo's Python/Docker stack should build on ARM.
- Simple paid fallback: 2 vCPU / 4 GB RAM if capital is tight, with
  `PM_PAPER_MEM_LIMIT=2g`.
- Safer paid fallback: 4 vCPU / 8 GB RAM if we want fewer memory pauses while
  model optimisation and websocket collection run together.

For any future live trading review, avoid US-hosted infrastructure because the
repo's governance notes treat US Polymarket access as geoblocked. Paper-only
monitoring can run anywhere, but keeping the VPS outside the US preserves the
future path.

## What this stack runs

`docker-compose.vps-paper.yml` starts only two services:

1. `polymarket-paper-live`: continuous websocket paper/evidence loop.
2. `polymarket-dashboard`: the static oversight cockpit on port `8765`.

It does not start the broad raw/wide stacks and it does not enable live orders.
The environment hard-codes:

```text
POLYMARKET_EXECUTE_LIVE=false
POLYMARKET_LIVE_TRADING=0
PM_MODE=scan
```

## First deploy

Fast path on a fresh Ubuntu or Oracle Linux VPS:

```bash
curl -fsSL https://raw.githubusercontent.com/daniedorfling18-maker/Claude/main/scripts/bootstrap_polymarket_vps_paper.sh | sh
```

If you prefer to review the commands first:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker

git clone https://github.com/daniedorfling18-maker/Claude.git
cd Claude
cp .env.vps-paper.example .env
```

If the VPS has only 4 GB RAM, edit `.env`:

```text
PM_PAPER_MEM_LIMIT=2g
POLYMARKET_WEBSOCKET_MAX_ASSETS=80
POLYMARKET_EVENT_LIMIT=100
```

Then start:

```bash
docker compose -f docker-compose.vps-paper.yml up -d --build
docker compose -f docker-compose.vps-paper.yml ps
docker compose -f docker-compose.vps-paper.yml logs -f polymarket-paper-live
```

The bootstrap script leaves an existing `.env` alone. On a fresh `.env`, it
automatically applies leaner settings if it detects a 4 GB VPS. On Oracle Linux,
it uses `dnf`/`yum` instead of `apt-get`, so prefer the fast path unless you
deliberately want a manual install.

## Dashboard access

The dashboard container listens on port `8765`.

If the VPS firewall allows the port, open:

```text
http://<VPS_PUBLIC_IP>:8765/
```

Do not leave this publicly open forever. It can expose strategy state and P&L.
For a longer-running setup, prefer one of:

- a firewall rule that allows only your current IP;
- Tailscale on the VPS and your phone, then open `http://<tailscale-ip>:8765/`;
- a reverse proxy with authentication.

## Operating checks

```bash
bash scripts/check_polymarket_vps_paper.sh
docker compose -f docker-compose.vps-paper.yml ps
docker compose -f docker-compose.vps-paper.yml logs --tail=80 polymarket-paper-live
docker stats --no-stream
```

Useful files inside the mounted repo:

```text
outputs/polymarket_model_governance/local_live_loop_heartbeat.json
outputs/polymarket_model_governance/forward_paper_cycle.json
outputs/polymarket_model_governance/trade_signal_audit.json
outputs/polymarket_dashboard/dashboard_data.json
```

## Stop / upgrade

```bash
docker compose -f docker-compose.vps-paper.yml down
git pull
docker compose -f docker-compose.vps-paper.yml up -d --build
```

Keep one compose stack running at a time. Do not run
`docker-compose.polymarket-wide-raw.yml` on the VPS unless deliberately doing a
bounded research job; it is not the live paper stack.
