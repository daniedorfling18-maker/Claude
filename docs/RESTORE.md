# Polymarket ledger disaster recovery (WO-65)

This runbook restores the investor evidence ledgers, not the large training or
websocket corpora. The recovery objective is **RTO under one day**. At the
paper stage the maximum recovery-point objective (RPO) is **7 days / 168
hours**. Before any wallet or other live capital is configured,
`disaster_recovery.active_rpo_hours` must be changed, with a dated config
comment, to **24 hours or less**; the archive builder refuses to proceed if
that tightening has not happened. The tracked VPS configuration was tightened
to **24 hours on 2026-07-13** when the public maker-test wallet became
configured; the 168-hour value remains only the maximum for a wallet-free
paper stage.

The current snapshot is force-replaced on the private `vps-archive` branch to
bound storage. Historical proof does not depend on that branch's Git history:
the restored files must verify against WO-61's append-only chain and its
durable `vps-anchor` timestamps.

## What the archive contains

- Every file referenced by every WO-61 chain manifest through the snapshot
  date, including immutable snapshots of rewritten state tables.
- The WO-61 chain, current head, summary, and verification artifact.
- WO-63's append-only cost ledger when it exists.
- An internal manifest with a SHA-256 digest and byte length for every file.

It excludes secrets, `.env`, wallet keys, databases, model/training corpora,
websocket features, trade-print archives, and official-book archives. The
compressed and expanded archive are both hard-capped at 50MB.

## Fresh VPS to a verified recovery

1. Create a fresh Ubuntu VPS, install Git and Docker, clone the private repo to
   `/home/opc/Claude`, and check out `main`.
2. Copy only the operational `.env`/secrets from the approved secret store.
   Never source secrets from `vps-archive`; they are intentionally absent.
3. Build the deployment image, but do not start the recurring writer stack yet:

   ```sh
   cd /home/opc/Claude
   docker compose -f docker-compose.vps-paper.yml build
   ```

4. Fetch and cryptographically test the remote snapshot without touching the
   destination ledgers:

   ```sh
   sh scripts/restore_from_archive.sh --dry-run --repo-dir /home/opc/Claude
   ```

   A successful result must say `status: ok`, `restore_applied: false`, and
   show WO-61 verified through the archive's snapshot date. Any non-zero exit
   is a failed recovery test; do not start the stack.
5. Ensure the destination is empty. On a genuinely fresh host this normally
   means `/home/opc/Claude/outputs` does not yet exist or has no files. If an
   attempted deployment already created it, stop all containers and preserve
   that directory under a separately named incident-evidence path before
   creating a new empty `outputs` directory. Never overlay a restore onto
   existing evidence.
6. Apply the already-tested archive:

   ```sh
   sh scripts/restore_from_archive.sh --apply \
     --repo-dir /home/opc/Claude \
     --output-root /home/opc/Claude/outputs
   ```

7. Repeat the dry run against the local archive if desired, then start exactly
   one stack:

   ```sh
   docker compose -f docker-compose.vps-paper.yml up -d
   ```

8. Confirm the dashboard, scheduler, wallet reconciliation (when configured),
   cost ledger, current ledger-chain verification, and telemetry status. The
   first new daily anchor must extend the restored chain rather than start a
   new genesis chain.

## Routine proof and failure handling

The existing host telemetry cadence calls `scripts/push_vps_archive.sh`. It
builds only when the active RPO is due, verifies WO-61 before packaging, and
force-pushes one parentless commit to `vps-archive`. Every build/runtime/size or
push failure updates
`outputs/performance/disaster_recovery_status.json` before telemetry is
collected, so a missed recovery point is externally visible and retried on the
next telemetry cycle.

For an operator-supplied local archive:

```sh
sh scripts/restore_from_archive.sh --dry-run \
  --repo-dir /home/opc/Claude \
  --archive /secure/path/ledger_state_archive.tar.gz
```

Do not fund or resume live capital when the last successful remote archive is
older than the active RPO, the archive status is not `ok`, or either the
archive-digest or WO-61 chain verification fails.
