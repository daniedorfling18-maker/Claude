# Polymarket Docker safety boundary

Legacy Compose surfaces remain in the repository for history/regression only.
The single supported production project is `docker-compose.vps-paper.yml` with
four long-running services; all runtime and verification are VPS-only and a
duplicate writer is prohibited.

This note is not an approval checkpoint. Funding is CLOSED, WO-67 is BLOCKED
and current health/private transport must be proved by fresh generated VPS
evidence. The 2026-07-21 audit found unresolved Funnel-off, acceptance,
healthcheck, secret-scope and root-container gaps; see
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.
