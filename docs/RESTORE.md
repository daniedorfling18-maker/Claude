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
  date, including immutable snapshots of rewritten state tables, except the
  registered exclusions below.
- The WO-61 chain, current head, summary, and verification artifact.
- WO-63's append-only cost ledger when it exists.
- An internal manifest with a SHA-256 digest and byte length for every file,
  plus the registered exclusion prefixes and the path/size of every file they
  dropped.

It excludes secrets, `.env`, wallet keys, databases, model/training corpora,
websocket features, trade-print archives, and official-book archives. The
compressed and expanded archive are both hard-capped at 240MB
(2026-07-26 owner amendment; 50MB until the append-only ledger set outgrew it).

### Registered archive exclusions (WO-123, 2026-07-26 owner decision)

`polymarket_training/` is enrolled in the WO-61 chain but is **not** archived.
It is a derived collection corpus — regenerable by re-harvest — and it was 94%
of the archive bytes (476.6MB of a 505.6MB set on 2026-07-26, one file at
467.1MB), which is what pushed the archive over the cap.

The exclusion is scoped narrowly:

- The corpus stays **anchored**, so the chain keeps recording its digests and
  every anchor written on a tree that still holds the corpus is fully verified.
  Only the recovery archive is smaller. What a *restore* does to that evidence is
  stated in the next section — it is not "unchanged", and it must not be read as
  such.
- `disaster_recovery.excluded_path_prefixes` may only **shrink** the registered
  set. Removing a prefix puts that corpus back into the archive; adding an
  unregistered prefix is ignored, so config can never quietly drop a ledger
  from recovery.
- An archive that declares a prefix excluded while also **including** a file
  under it is refused outright (WO-127). The declaration grants verification
  tolerance, so an archive supplying the very bytes it claims to have dropped
  would be granting itself tolerance for its own payload. No archive this code
  builds takes that shape.
- After a restore, the excluded corpus is re-harvested by the normal collection
  cadence. Recovery of the investor evidence ledgers does not wait on it.

### What a restore does to tamper evidence over the excluded corpus (WO-127)

An applied restore writes
`outputs/performance/ledger_restore_provenance.json`, recording the excluded
prefixes it could not restore and the archive's snapshot date as the **restore
boundary**. `verify-ledger-chain` and the production `anchor-ledgers` run both
read that marker, and both treat it the same way — the marker is a property of
the tree, not an option a caller passes.

For a restored tree, therefore:

- Manifest entries under a **registered and declared** prefix belonging to rows
  anchored **at or before the boundary** are **unverifiable by design**. Neither
  absence nor a changed digest breaks the chain for those entries, and the count
  is reported as `restored_unverifiable_tolerated` in the verification artifact
  (and in the restore report). This is a deliberate waiver, not an oversight: the
  archive dropped those bytes, and a re-harvest produces *different* content, so
  the entry would otherwise flip from "missing" to "digest changed" and wedge the
  chain permanently the first time collection ran.
- Every entry **outside** those prefixes, and every row anchored **after** the
  boundary, is verified exactly as before. A post-boundary row records
  `missing_at_anchor` until re-harvest and then `present` with fresh digests that
  do verify, so tamper evidence over the corpus resumes from the boundary
  forward. A present file with a changed anchored digest outside the waiver is
  still a broken chain.
- The marker **travels with the tree**. Every archive built on a restored host
  carries it, so a restore of that archive inherits the same boundary. Narrowing
  `excluded_path_prefixes` on a restored host (putting the corpus back into
  recovery) therefore still builds and still verifies: the inherited boundary
  keeps the pre-restore rows excused, while the regenerated corpus the new archive
  carries is byte-verified normally from the boundary forward. An archive that
  drops the corpus again extends the boundary to its own snapshot date; one that
  carries it does not.
- The boundary is **bound to the chain**, not merely to the calendar. A marker
  records the chain head it was restored from, and the boundary must be the anchor
  date of the row carrying that head. Editing either field alone breaks the pairing
  and refuses the marker.
- **What that binding does not prove.** It proves the pair names a real link in
  this chain; it does not authenticate the restore event. Anyone who can write
  `outputs/` can also read the chain file and copy a later genuine row's date and
  head, moving the waiver forward that far. The residual is bounded to the
  registered prefixes, to rows at or before the forged boundary, and to an attacker
  who already has write access to the output tree — the same access that can
  rewrite the whole chain self-consistently, since verification recomputes heads
  from the manifests it is handed. Local bytes are not the root of trust for
  either: the externally pushed `vps-anchor` branch is, and it is what makes a
  local rewrite of a chain row or of this marker detectable. Closing the residual
  means enrolling the marker in the chain itself, which is a registered-surface
  change tracked as its own work order.
- The marker is **refused** — and the reason reported in
  `restore_provenance_rejected` — when it names no registered prefix, is
  unreadable or not a JSON object, carries a boundary that is not a canonical
  `YYYY-MM-DD` date or is in the future, or carries a boundary that does not match
  the anchor date of its recorded chain head. A refused marker excuses nothing:
  every anchored path is verified.

Operator reading: on a restored tree, `status: ok` means *the chain verified
except for `restored_unverifiable_tolerated` boundary-scoped entries under the
declared prefixes*. Check that counter and `restore_boundary_date`; a non-zero
counter on a tree that was never restored, or a boundary later than the restore
you performed, is itself the finding.

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
   new genesis chain, and it must return `ok` rather than
   `blocked_broken_chain` — on a restored tree that requires
   `ledger_restore_provenance.json` to be present and honoured, so check
   `restore_boundary_date`, `restore_tolerated_prefixes`, and
   `restored_unverifiable_tolerated` in
   `outputs/performance/ledger_anchor_verification.json` against the archive you
   restored, and check `restore_provenance_rejected` is null.

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
