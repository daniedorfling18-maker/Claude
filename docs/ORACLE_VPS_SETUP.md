# Oracle VPS setup checklist

Use this once the Oracle Always Free VM is provisioned. The current Polymarket
VPS lane is the lean websocket paper stack, not the older monitor stack.

1. SSH into the Ubuntu VM.
2. Install Docker and Docker Compose.
3. Clone this repo.
4. Copy `.env.vps-paper.example` to `.env`.
5. Keep paper/dry-run settings enabled.
6. Start the paper stack with `docker compose -f docker-compose.vps-paper.yml up -d --build`.
7. Review logs with `docker compose -f docker-compose.vps-paper.yml logs -f polymarket-paper-live`.
8. Open the dashboard at `http://<VPS_PUBLIC_IP>:8765/` after allowing the port or setting up Tailscale.

Full instructions: `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`.

Do not commit `.env` or private keys.
