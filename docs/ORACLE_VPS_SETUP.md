# Oracle VPS setup checklist

Use this once the Oracle Always Free VM is provisioned. The current Polymarket
VPS lane is the lean websocket paper stack, not the older monitor stack. Oracle
Linux uses the `opc` SSH user; Ubuntu uses `ubuntu`.

1. SSH into the VM.
2. Run `curl -fsSL https://raw.githubusercontent.com/daniedorfling18-maker/Claude/main/scripts/bootstrap_polymarket_vps_paper.sh | sh`.
3. Review status with `bash ~/Claude/scripts/check_polymarket_vps_paper.sh`.
4. Open the dashboard at `http://<VPS_PUBLIC_IP>:8765/` after allowing the port or setting up Tailscale.

Full instructions: `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`.

Do not commit `.env` or private keys.

## Runtime-state-safe updates

The VPS checkout is also the bind-mount root for live paper evidence, so
collectors legitimately modify files under `data/`, `inputs/`, `outputs/`, and
`work/`. Bootstrap and GitHub deploys use
`scripts/update_vps_checkout_preserving_runtime.py` instead of a plain
`git pull`: it temporarily preserves tracked runtime changes, fast-forwards the
source, and reapplies the evidence. It refuses source/config edits or any path
collision rather than resetting data. Untracked corpora, including the SQLite
ledger, are never moved or cleaned.

The Docker build context excludes runtime corpora, ledgers, tests, docs, Git
history, and `.env`; this prevents multi-gigabyte paper datasets from being
uploaded into BuildKit on every deploy. After restart, `vps-ops-scheduler` is
the sole full-governance owner. Deployment waits for a newly generated
price-action model and then performs a reporting-only dashboard render; it
does not launch a competing governance refresh.
