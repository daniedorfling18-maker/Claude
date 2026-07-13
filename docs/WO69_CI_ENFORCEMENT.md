# WO-69 independent merge gate

The mandatory check is `WO-69 guard and invariants` in
`.github/workflows/required-pr-gate.yml`. It is deliberately small: ruff, both config validators,
front-door operating-state drift, execution/governance fail-closed tests, risk invariants, promotion
gates, and Docker/scheduler/telemetry contract tests. The comprehensive suite remains a local/manual
pre-merge control.

## Runner

The selected owner-infrastructure option is a repository-scoped Linux ARM64 runner on the upgraded
Oracle VPS, labelled:

```text
self-hosted, Linux, ARM64, polymarket-ci
```

It is installed outside the repository at `/opt/actions-runner-polymarket-ci-vps` and starts as the
systemd service `actions.runner.daniedorfling18-maker-Claude.oracle-vps-polymarket-ci.service`, running
as `opc`. It has no repository or trading secrets; the job grants only `contents: read` and does not
persist checkout credentials. Each gate runs in a disposable `python:3.11-slim` container capped at
2 CPUs and 4 GiB, so CI no longer depends on the laptop and cannot consume the whole VPS.

Each job creates a disposable virtual environment in that container, installs the proposed
repository and development dependencies from `pyproject.toml`, and runs `pip check`. The first run
may populate Docker/Python caches; the 15-minute job limit remains fail-closed.

Operator checks:

```bash
sudo systemctl status actions.runner.daniedorfling18-maker-Claude.oracle-vps-polymarket-ci.service
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
