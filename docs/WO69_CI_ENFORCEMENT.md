# WO-69 independent merge gate

The mandatory check is `WO-69 guard and invariants` in
`.github/workflows/required-pr-gate.yml`. It is deliberately small: ruff, both config validators,
front-door operating-state drift, execution/governance fail-closed tests, risk invariants, promotion
gates, and Docker/scheduler/telemetry contract tests. The comprehensive suite remains a local/manual
pre-merge control.

## Runner

The selected owner-infrastructure option is a repository-scoped Windows self-hosted runner labelled:

```text
self-hosted, Windows, X64, polymarket-ci
```

It is installed outside the repository at `%USERPROFILE%\actions-runner-polymarket-ci` and starts as
the current-user scheduled task `GitHub Polymarket CI Runner`. It has no repository secrets; the job
grants only `contents: read` and does not persist checkout credentials. If the laptop is offline, the
check queues instead of silently passing.

Operator checks:

```powershell
Get-ScheduledTask -TaskName "GitHub Polymarket CI Runner"
gh api repos/daniedorfling18-maker/Claude/actions/runners
```

Do not commit or copy the runner's `.credentials*`, `.runner`, or `.credentials_rsaparams` files.

## Enforcement audit

Run the fail-closed audit from the repository root:

```powershell
python scripts/audit_github_merge_gate.py --repo daniedorfling18-maker/Claude
```

It writes `outputs/performance/independent_merge_gate.json`, which WO-68 consumes for WO-67 P4. A
missing check, offline runner, unsuccessful latest PR run, missing review rule, admin bypass, or direct
push path keeps `enforced=false`.

## Private-plan boundary

GitHub returned HTTP 403 for branch protection and rulesets on this private Free-plan repository. Do
not make the repository public to work around that boundary. After the owner upgrades the repository
to GitHub Pro/Team, apply the registered policy and immediately re-audit:

```powershell
python scripts/audit_github_merge_gate.py `
  --repo daniedorfling18-maker/Claude `
  --apply-protection
```

The registered policy requires the exact check context, an approval after the latest push, stale-review
dismissal, admin enforcement, no review bypasses, no direct pushes, no force pushes, and no deletion.
Further live capital remains blocked until the generated artifact reports `status=enforced`.
