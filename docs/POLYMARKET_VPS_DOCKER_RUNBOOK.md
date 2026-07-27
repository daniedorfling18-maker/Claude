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

## GitHub deploy path

After the first deploy, use the manual GitHub Actions workflow
`deploy-polymarket-vps-paper.yml` to keep the VPS current without opening an
interactive SSH session from the laptop.

Required repository secrets:

```text
PM_VPS_HOST=129.151.178.42
PM_VPS_USER=opc
PM_VPS_SSH_PRIVATE_KEY=<the private key matching the VPS public key>
```

Recommended repository secret:

```text
THE_ODDS_API_KEY=<the-odds-api key>
```

Optional secrets:

```text
PM_VPS_PORT=22
PM_VPS_REPO_DIR=~/Claude
```

The workflow itself runs on the provisioned `oracle-vps-polymarket-ci` runner,
not billable `ubuntu-latest` capacity. It validates target capacity before
touching the healthy stack, quiesces bind-mounted writers, preserves runtime
evidence while fast-forwarding `main`, injects sealed secrets into `.env`, and
rebuilds `docker-compose.vps-paper.yml`. The scheduler owns exactly one
post-deploy governance refresh; deployment waits for a fresh price-action
model, renders the dashboard, runs `scripts/check_polymarket_vps_paper.sh`, and
verifies the current proof/evidence schema. A checkout refusal restores the
previous stack when HEAD was not changed.

## Deploy acceptance after any out-of-workflow deploy (WO-122, TS-15)

`AGENTS.md` requires production deploys to go through the
`Deploy Polymarket VPS Paper` workflow: it preserves runtime ledgers, stamps
`PM_VPS_DEPLOYED_SHA`, runs post-deploy acceptance, and retains a rollback
revision. An ad-hoc pull/rebuild is explicitly not a substitute, so this runbook
does not document one — normalising a manual deploy path is an owner governance
decision, not a runbook edit.

What the guarded path gives you and a hand-run stack does not, in order of how
much it hurts to lose it:

1. **A rollback revision.** Nothing else retags the last-known-good image, so a
   bad deploy has no automatic way back.
2. **WO-79 acceptance against the target SHA.** Without it,
   `deploy_acceptance.json` keeps reporting the PASS from whichever revision last
   ran it, which can be several revisions stale.
3. **Marker ordering.** Every service interpolates
   `PM_IMAGE_BUILD_SHA: ${PM_VPS_DEPLOYED_SHA}` at container-create time, so
   starting containers *before* `.env` and `outputs/performance/deployed_git_rev`
   are updated leaves the running stack labelled with the previous deploy. WO-126
   makes that visible: the operating-state deployment block compares the SHA
   **baked into the image** against the checkout and reports `IMAGE_DRIFTED`.

If a deploy did happen outside the workflow (an emergency, or Actions being
unavailable), the stack is not trustworthy until acceptance has been re-run
against the current SHA. The scheduler must be **absent** while acceptance runs —
it is the sole full-governance owner and a concurrent pass invalidates the
result. Run from the repo root; running these from `~` silently 404s each script:

```bash
cd ~/Claude
# The markers must be correct BEFORE containers are recreated, or the running
# stack carries the previous deploy's image marker (see point 3 above).
git rev-parse HEAD > outputs/performance/deployed_git_rev
sed -i "s/^PM_VPS_DEPLOYED_SHA=.*/PM_VPS_DEPLOYED_SHA=$(git rev-parse HEAD)/" .env
python3 scripts/write_vps_telemetry_manifest.py \
  --repo-root . --output outputs/performance/vps_telemetry_manifest.json

docker compose -f docker-compose.vps-paper.yml stop vps-ops-scheduler
docker compose -f docker-compose.vps-paper.yml --profile deploy-acceptance \
  run --rm --no-deps vps-deploy-acceptance
docker compose -f docker-compose.vps-paper.yml up -d vps-ops-scheduler
```

Then verify:

```bash
bash scripts/check_polymarket_vps_paper.sh
```

The health check now enforces freshness ceilings, fails on a missing or
container-invisible `THE_ODDS_API_KEY`, fails when the dashboard container
recorded a failed startup render, and fails closed when Tailscale funnel state
cannot be established — so a frozen, half-configured or stale-serving stack no
longer passes the gate.

## Dashboard access

The dashboard backend is deliberately bound only to VPS loopback on port
`8765`. There is no supported public-IP HTTP route. Authenticated HTTPS is
provided by Tailscale Serve, which is tailnet-only; Tailscale Funnel is
explicitly forbidden.

One-time setup on the VPS:

```bash
# Install from the official Linux instructions if tailscale is not present:
# https://tailscale.com/download/linux
sudo tailscale up
# Complete the browser login, using the same tailnet as the phone/laptop.
cd /home/opc/Claude
bash scripts/configure_polymarket_dashboard_tailscale.sh
```

The script closes any old Docker public binding before enabling Serve, disables
Funnel on HTTPS port 443, writes the node's `https://...ts.net/` URL to
`PM_DASHBOARD_PUBLIC_URL`, verifies the exact Serve target and Docker binding,
and writes `outputs/performance/dashboard_private_transport.json`. It refuses
to proceed when the node is not authenticated.

Install Tailscale on the phone, join the same tailnet, and open the private URL
printed by the script. Remove the old Oracle Cloud ingress rule for TCP `8765`;
the loopback binding already blocks it, and removing the rule gives a second
independent control. Operators can read the configured URL without exposing
other `.env` values:

```bash
grep '^PM_DASHBOARD_PUBLIC_URL=' /home/opc/Claude/.env
```

Every deploy requires an authenticated Tailscale node before quiescing the
current stack and revalidates loopback binding, Serve HTTPS, URL provenance,
and Funnel-off state before reporting success.

## Secrets on the VPS

Preferred path: set `PM_VPS_SSH_PRIVATE_KEY` in GitHub Actions secrets and run the manual
`deploy-polymarket-vps-paper.yml` workflow. The workflow injects `THE_ODDS_API_KEY` into the VPS
`.env`, rebuilds the Docker stack, waits for the scheduler-owned model refresh, and verifies the
current dashboard schema. If the deploy private key is missing, GitHub can see the sealed odds key
but cannot deliver it to the VPS.

Manual fallback: place any key the containers need (today: `THE_ODDS_API_KEY` for the sharp-anchor
pipeline) in the `.env` file next to the compose file by hand:

```bash
cd <repo dir on the VPS>
nano .env                      # set THE_ODDS_API_KEY=<key>
docker compose -f docker-compose.vps-paper.yml up -d --force-recreate polymarket-paper-live
```

Two gotchas that make this look broken when it isn't:

1. `docker compose restart` does NOT reload `env_file` - you must `up -d --force-recreate`.
2. The variable must appear both in `.env` AND be mapped in the service's `environment:` block
   (`docker-compose.vps-paper.yml` already maps `THE_ODDS_API_KEY`).

Verify end-to-end with `scripts/check_polymarket_vps_paper.sh` (it reports whether the key is set in
`.env`, visible inside the container, and whether deployment metadata is present) or on the
dashboard: the "Independent model anchors" section should stop showing `missing_api_key` within
~12 loop iterations.

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
