# VPS paper-live restart forensics — 2026-07-12

## Verdict

The restart sequence was deterministic application supervision, not Docker
instability and not an out-of-memory kill. The live-loop process deliberately
called `os._exit(75)` whenever a background worker exceeded its registered
maximum runtime. Docker then applied `restart: unless-stopped` exactly as
configured.

The trigger was duplicated full-governance work on an under-capacity host. The
1-vCPU VPS ran discovery, prediction, websocket collection, and an in-loop full
governance refresh while `vps-ops-scheduler` also owned full governance. The
in-loop pass repeatedly crossed its 600-second supervisor limit.

## Evidence

- Docker journal entries identify `exitCode=75`, `manualRestart=false`, and
  `restartPolicy={unless-stopped 0}`.
- The original container reached restart count 9. After its 09:38 UTC
  recreation, the replacement reached restart count 6, with recorded exits at
  10:08:37, 10:49:42, 11:04:22, 11:48:58, 12:12:47, and 12:26:22 UTC.
- Docker inspection reported `OOMKilled=false`; no kernel OOM event explained
  the exits.
- The last duplicated governance pass ran from 12:27:51 to 12:34:07 UTC (376
  seconds). That is below the 600-second limit but still competes with the hot
  websocket/paper bridge for the only CPU.
- After recreating only `polymarket-paper-live` at 12:36:25 UTC with
  `POLYMARKET_GOVERNANCE_REFRESH_SECONDS=0`, it remained at restart count 0
  during the verification window. Data volumes were not removed or reset.

## Permanent correction

1. `vps-ops-scheduler` is the sole owner of full governance. The Compose and
   VPS environment defaults set the duplicate live-loop cadence to zero.
2. The websocket and 30-second paper bridge remain continuously live.
3. Future supervisor exits append to
   `outputs/performance/background_timeout_incidents.csv` before exit 75. That
   ledger is enrolled in the WO-61 prefix-hash anchor.
4. Scheduler status now carries run, failure, skip, consecutive-skip, and
   duration counters for generated SLO reporting.
5. Deployment preflight evaluates the target revision before changing the
   mounted checkout. It requires at least 2 vCPUs, 6 GiB free disk, 512 MiB
   currently available memory, and physical RAM no lower than the sum of all
   Compose `mem_limit` values. Failure returns
   `REFUSE_DEPLOY_KEEP_EXISTING_STACK` before `docker compose up`.

The current 1-vCPU / roughly 5.5-GiB host does not satisfy the registered target
capacity for the default 7.25-GiB Compose memory commitment. This is an expected
deployment refusal, not a reason to weaken the preflight. The running mitigation
stays in place until capacity is upgraded or the declared service envelope is
re-architected in a separately reviewed work order.

All controls in this incident correction are reporting/operations controls.
They do not place orders or change any trading gate.
