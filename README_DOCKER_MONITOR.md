# Docker monitor quick start

Use the monitor-specific compose file:

```bash
cp .env.example .env
docker compose -f docker-compose.monitor.yml up -d --build
docker compose -f docker-compose.monitor.yml logs -f polymarket-monitor
```

The monitor compose file forces dry-run mode:

```env
PM_MODE=dry_run
POLYMARKET_EXECUTE_LIVE=false
```

Review outputs under `outputs/polymarket/`.
