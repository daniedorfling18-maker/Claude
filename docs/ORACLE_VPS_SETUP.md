# Oracle VPS setup checklist

Use this once the Oracle Always Free VM is provisioned.

1. SSH into the Ubuntu VM.
2. Install Docker and Docker Compose.
3. Clone this repo.
4. Copy `.env.example` to `.env`.
5. Keep dry-run settings enabled.
6. Start the monitor with `docker compose -f docker-compose.monitor.yml up -d --build`.
7. Review logs with `docker compose -f docker-compose.monitor.yml logs -f polymarket-monitor`.

Do not commit `.env` or private keys.
