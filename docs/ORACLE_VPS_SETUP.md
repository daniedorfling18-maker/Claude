# Oracle VPS setup checklist

Use this once the Oracle Always Free VM is provisioned. The current Polymarket
VPS lane is the lean websocket paper stack, not the older monitor stack.

1. SSH into the Ubuntu VM.
2. Run `curl -fsSL https://raw.githubusercontent.com/daniedorfling18-maker/Claude/main/scripts/bootstrap_polymarket_vps_paper.sh | sh`.
3. Review status with `bash ~/Claude/scripts/check_polymarket_vps_paper.sh`.
4. Open the dashboard at `http://<VPS_PUBLIC_IP>:8765/` after allowing the port or setting up Tailscale.

Full instructions: `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`.

Do not commit `.env` or private keys.
