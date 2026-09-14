# VPS Docker dry-run monitor

This guide sets up an Ubuntu VPS with Docker for dry-run monitoring. It is intended for logging and review, not unattended execution.

## 1. Prepare the VPS

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y ca-certificates curl git ufw
```

## 2. Install Docker

```bash
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

Check Docker:

```bash
docker --version
docker compose version
```

## 3. Clone the repo

```bash
git clone https://github.com/daniedorfling18-maker/Claude.git
cd Claude
```

For a private repo, use GitHub SSH or a GitHub token.

## 4. Create the environment file

```bash
cp .env.example .env
nano .env
```

Recommended dry-run settings:

```env
PM_MODE=dry_run
POLYMARKET_EXECUTE_LIVE=false
POLYMARKET_QUERY=world cup
POLYMARKET_SCAN_SECONDS=270
POLYMARKET_SCAN_INTERVAL_SECONDS=5
POLYMARKET_OUTPUT_DIR=outputs/polymarket
```

## 5. Start the monitor

```bash
docker compose up -d --build
```

View logs:

```bash
docker compose logs -f polymarket-monitor
```

Stop the monitor:

```bash
docker compose down
```

## 6. Review outputs

Outputs are written under:

```text
outputs/polymarket/
```

Keep this service in dry-run mode while validating the data, logs, and outputs.
