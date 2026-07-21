# Oracle VPS setup boundary

This is a provisioning checklist, not a deployment script. The canonical
production location is `/home/opc/Claude` on the Oracle VPS, and runtime/testing
must never be moved back to a workstation.

1. Provision the VM and restrict inbound firewall rules; TCP `8765` must not be
   publicly reachable.
2. Establish reviewed, authenticated access to this private repository. Do not
   pipe a raw GitHub URL to a shell and do not start the legacy bootstrap path.
3. Install and enroll Tailscale, with Funnel disabled. The dashboard must remain
   loopback-bound and be served only to authenticated tailnet members.
4. Place secrets only in the VPS `.env` or GitHub Actions secrets. Never commit
   them or copy them into telemetry.
5. Make the first and every later production start through the guarded
   `Deploy Polymarket VPS Paper` workflow from an independently accepted `main`
   revision.
6. Treat missing or stale acceptance/operating-state evidence as `UNKNOWN`.

The workflow preserves runtime roots, isolates the one-shot acceptance writer
from the scheduler and retains rollback material. It must not be replaced by an
ad-hoc pull/rebuild. The 2026-07-21 static audit nevertheless found open Funnel,
acceptance, host-key and bootstrap gaps; consult
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md` before relying on the path.

Full operator detail is in `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`. Funding is
CLOSED, WO-67 is BLOCKED and no autonomous live-order path is approved.
