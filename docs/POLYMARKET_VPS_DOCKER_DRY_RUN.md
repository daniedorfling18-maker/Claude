# Polymarket VPS Docker dry-run monitor

This setup runs the Polymarket scanner continuously as a dry-run monitor on an Ubuntu VPS. It writes outputs for review and keeps live execution disabled.

## Safety defaults

```env
PM_MODE=dry_run
POLYMARKET_EXECUTE_LIVE=false
```

Do not place private keys in `.env` while validating dry-run output.

## Install Docker on Ubuntu

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y ca-certificates curl git ufw

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

## Clone and configure

```bash
git clone https://github.com/daniedorfling18-maker/Claude.git
cd Claude
cp .env.example .env
nano .env
```

Recommended `.env` values:

```env
PM_MODE=dry_run
POLYMARKET_EXECUTE_LIVE=false
POLYMARKET_QUERY=world cup
POLYMARKET_SCAN_SECONDS=270
POLYMARKET_SCAN_INTERVAL_SECONDS=5
POLYMARKET_OUTPUT_DIR=outputs/polymarket
```

Create the model-probabilities file:

```bash
cp inputs/polymarket/model_probabilities.example.csv inputs/polymarket/model_probabilities.csv
nano inputs/polymarket/model_probabilities.csv
```

## Start the monitor

```bash
docker compose -f docker-compose.monitor.yml up -d --build
```

View logs:

```bash
docker compose -f docker-compose.monitor.yml logs -f polymarket-monitor
```

Stop the monitor:

```bash
docker compose -f docker-compose.monitor.yml down
```

## Outputs

Review these files before taking any manual action:

```text
outputs/polymarket/market_snapshot.csv
outputs/polymarket/opportunities.csv
outputs/polymarket/execution_log.csv
```
