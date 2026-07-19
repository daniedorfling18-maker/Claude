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
price-action model, stops the recurring scheduler, and runs component
acceptance in the profiled one-shot `vps-deploy-acceptance` service. The
scheduler must be absent before that service starts and is restarted only
after acceptance passes.

The deploy workflow requires the run ID of the successful `Independently
Reviewed PR Merge` run that produced the exact current `main` SHA. It verifies
the workflow identity and its `merge-attestation.json` before contacting the
VPS. Missing, malformed, stale-SHA, non-independent, or non-successful
attestation evidence refuses deployment.

Before quiescing the stack, deployment requires the checkout and deployed
marker to match, snapshots `.env` and the marker with owner-only permissions,
and tags the running image as `polymarket-paper-vps:rollback-last-known-good`.
Every non-zero exit after that boundary automatically runs
`scripts/rollback_vps_paper_deploy.py`: source returns to the captured commit
without deleting runtime roots, the exact environment and marker are restored,
the prior image is retagged, all four production services are recreated, and
the ordinary VPS health check must pass. A failed rollback remains a failed
deployment, writes `outputs/performance/vps_deploy_rollback.json`, and retains
secret-bearing recovery material mode-0700 for manual intervention; it never
prints that material.
