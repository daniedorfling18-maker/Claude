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

## Correction — the obvious fix was drafted, gated, and REJECTED

The obvious correction is an off-box alarm that reads the age of the last commit
on `origin/vps-telemetry` from a GitHub-hosted runner. It was drafted as WO-165,
put through an independent S8 gate, and returned **NOT ADMISSIBLE** on A1, A2,
A5, A9, A10 and A11. The findings are recorded here because they rule out an
entire class of fix, and the next person to reach for it should not have to
rediscover them.

**It cannot be installed while the VPS is down.** GitHub fires `schedule:`
triggers only on the repository's default branch. Reaching `main` requires the
required PR gate, which runs on the self-hosted runner on the VPS. So the
detector cannot be deployed during exactly the outage it exists to detect, and
the claim that it "cannot be taken down by the failure it detects" is false.

**Mirror freshness is not system liveness.** `scripts/push_vps_telemetry.sh`
skips missing producer directories (`[ -d ... ] || continue`, `:167-168`) and
missing files (`[ -f ... ] || return 0`, `:148`), then copies the manifest,
mints a commit and force-pushes regardless (`:187-197`). A fresh mirror commit
proves the crontab ran. It does not prove any engine is alive. An alarm on that
signal reads GREEN while every producer on the host is dead.

**The timestamp is written by the subject under test.** `commit-tree` at
`:194-196` pins no `GIT_COMMITTER_DATE`, so the age is computed from the
monitored host's own clock. A forward-skewed clock extends the green-after-death
window and lets a fired alarm silently self-clear.

**A factual error in the draft, recorded rather than quietly fixed.** It claimed
the telemetry push cadence "is not in the repository". It is:
`scripts/push_vps_telemetry.sh:19` documents `*/30 * * * *`. The threshold was
therefore justified on a premise that was false, in the same paragraph that
claimed to be recording what could not be established.

**Every identified bias channel points at "no alarm"** — content blindness,
host-controlled clock, a threshold at 16x the true cadence, and GitHub's
schedule delay or 60-day inactivity auto-disable. Under A11 that is
inadmissible absent an argument the effect exceeds the aggregate bias, and no
such argument exists.

**What would actually work, and why it is not a work order.** The detector must
sit outside both the VPS and this repository's CI, and it must push a
notification rather than set a check status — the harm here was that nobody
looked for 15 days, and a red run in a repo nobody opened for 12 days reproduces
that exactly. That means an external uptime monitor or an owner-side alert, which
is a tooling decision for the owner, not a change to this codebase. Restoring the
schedule on `.github/workflows/polymarket-vps-proof-health.yml` remains an option
but inherits the default-branch and notification problems above, and would
reverse a registered decision (`docs/POLYMARKET_CODEX_WORK_ORDERS.md` records
that the orchestrator must not self-provision recurring autonomy), so it stays
with the owner.

**The general rule still holds and is the durable lesson:** a liveness check that
executes on the subject it monitors is not a liveness check. Any consolidation of
recurring jobs onto the VPS must leave at least one observer outside it — and
that observer must be reachable, must measure something the subject cannot
forge, and must reach a human.

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
