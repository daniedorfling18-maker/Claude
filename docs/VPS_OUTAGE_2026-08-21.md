# VPS outage — telemetry silent since 2026-08-21

## Verdict

The host stopped publishing at **2026-08-21T02:00:09Z** and has published
nothing in the 15 days since. Root cause is NOT established in this record: the
box is unreachable, so nothing on it can be inspected. What is established is
the timeline, the blast radius, and — the part worth keeping — why nobody
noticed for two weeks.

**The outage was invisible because the VPS's own liveness monitoring runs on the
VPS.** `scripts/run_vps_ops_scheduler.sh` owns every recurring job, and
`degraded_state_watchdog.py` — the component whose entire purpose is noticing
that a producer has gone stale — executes on the same host it is watching. When
the host stopped, the watchdog stopped with it, and a stale-producer alarm that
cannot run is indistinguishable from no alarm at all.

There is exactly one health check that runs off the box,
`.github/workflows/polymarket-vps-proof-health.yml` on `ubuntu-latest`, and it
already accepts a `stale_after_minutes` input defaulting to **480**. It would
have flagged this within 8 hours. It did not fire because its schedule was
removed on 2026-07-09 with the note: *"schedule removed - all recurring jobs run
on the VPS ops scheduler now (scripts/run_vps_ops_scheduler.sh); Actions is
dispatch-only."* That consolidation is what moved the watchdog inside the thing
it watches.

## Evidence

- Last telemetry commit on `origin/vps-telemetry`: `fcebaa20`, authored
  2026-08-21 02:00:11 +0000, subject `vps telemetry snapshot 2026-08-21T02:00:09Z`.
- `telemetry/manifest.json` at that snapshot: `host: polymarket-trader`,
  `pushed_at_utc: 2026-08-21T02:00:09Z`, `deployed_git_rev: 036d09be`,
  `disk_used_percent: 65%`. The disk figure rules out a full-disk stop as of the
  last successful push.
- Required PR Gate runs 621-626 were created 2026-08-23 (12:36 to 13:25 UTC) and
  every one shows `updated_at` exactly 24 hours later with
  `conclusion: cancelled` — GitHub's queue timeout against a self-hosted runner
  that never accepted the job. So the runner was already unresponsive to work
  queued from 2026-08-23T12:36Z.
- No Required PR Gate runs exist between 2026-08-24 and 2026-09-05, because no
  commits were pushed in that window rather than because anything recovered.
- Runs 627 and 628, created 2026-09-05 against PR #454, show the same signature:
  627 cancelled, 628 queued.
- The evidence cannot distinguish "host down from 2026-08-21T02:00" from
  "degraded 08-21, fully unresponsive by 08-23". It establishes only that
  publishing stopped at the first instant and job acceptance had stopped by the
  second.

## Blast radius

- **No evidence accrued for any registered primary for 15 days.** All three are
  collected and evaluated on this host.
- **H2 reaches its fixed calendar stop of `2026-09-10T13:38:47Z` having accrued
  nothing since 2026-08-21.** On the last published scan
  (`event_group_consistency/event_group_scan.json`) it held
  `flagged_deviations: 0` and `flagged_with_executable_depth: 0` across
  `neg_risk_groups_scanned: 67`, with `max_executable_basket_usd: 0.0`. H2's
  support gate requires aggregate net profit positive, and zero flagged
  deviations yields zero profitable episodes, so the gate looks unmet
  independently of the outage. **That is an inference from the scan, not the
  verdict:** `outputs/h2_dutch/h2_evaluation.json` is the only artifact
  permitted to state the registered H2 verdict, it is not in the telemetry
  mirror, and it is unreadable while the host is down.
- **Eight pull requests are stalled** behind the dead runner: #454, #451, #450,
  #449, #447, #446, #417, #416.
- **WO-149's pending deploy did not happen.** The register records that deploy
  as the thing that moves `mb1_tier0_coverage_sufficient` off `false`, so the
  M-B maker gate has been frozen throughout.
- **Every figure quoted in the charter during the 2026-08-23 to 2026-09-05
  campaign carries an implicit asof of 2026-08-20/21**, because all of it was
  read from this snapshot. None of it has moved since.

## Correction proposed, not applied

The fix is to restore an off-box staleness alarm, and it is deliberately left
for the owner rather than applied here: re-adding a recurring schedule is
provisioning recurring autonomy, and the 2026-07-09 removal was a deliberate
registered decision that an agent should not silently reverse.

1. **Restore a `schedule:` trigger on
   `.github/workflows/polymarket-vps-proof-health.yml`.** It already runs on
   `ubuntu-latest` and already carries the 480-minute staleness threshold. This
   is the minimal change and would have cut a 15-day silence to under 8 hours.
2. **Prefer a check that needs no SSH.** The existing workflow authenticates to
   the host, so it fails when the host is unreachable — which is still a usable
   alarm, but a check on the age of the last `origin/vps-telemetry` commit needs
   no credentials and no reachable host, and cannot itself be taken down by the
   outage it detects.
3. **Record the general rule.** A liveness check that executes on the subject it
   monitors is not a liveness check. Any future consolidation of recurring jobs
   onto the VPS must leave at least one off-box observer.

Items 1 and 2 need their own registered work order. This document registers
nothing and authorises nothing.

## Restart order, when the host returns

1. `sudo systemctl restart actions.runner.daniedorfling18-maker-Claude.oracle-vps-polymarket-ci.service`
2. Re-run the gate on PR #454; merge if green. That lands the telemetry-mirror
   fix which makes the H2 and H3 verdict artifacts readable off-box at all.
3. `cat outputs/h2_dutch/h2_evaluation.json` and record the verdict in the
   charter as a dated result, before or after the 2026-09-10 stop.
4. Deploy WO-149.
5. Verify every collector resumed against the registered freshness SLOs in
   `degraded_state_watchdog.py`, which will be firing on all producers after a
   15-day gap.
6. Then the remaining queued pull requests, in dependency order.

All of this is reporting and operations. No trading gate, threshold or
eligibility rule is changed, and no order path is added or enabled.
