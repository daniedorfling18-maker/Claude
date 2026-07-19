# WO-69 independent merge gate

The mandatory check is `WO-69 guard and invariants` in
`.github/workflows/required-pr-gate.yml`. WO-100 rebuilt it on current main so
every PR now runs Ruff, both config validators, and the complete unfiltered
repository suite. A historical success is not reusable after a newer run starts
or fails.

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

The host checks out the proposed merge with Git available, then mounts it
read-only into a disposable `python:3.11-slim` container. The container copies
that checkout into its own filesystem, installs Git and the development
dependencies, runs `pip check`, and executes the complete gate. The first run
may populate Docker/Python caches; the 30-minute job limit remains fail-closed.

Operator checks:

```bash
sudo systemctl status actions.runner.daniedorfling18-maker-Claude.oracle-vps-polymarket-ci.service
gh api repos/daniedorfling18-maker/Claude/actions/runners
```

Do not commit or copy the runner's `.credentials*`, `.runner`, or `.credentials_rsaparams` files.

## Enforcement audit

Run the fail-closed audit from the VPS repository using its operator Python
environment and authenticated GitHub CLI:

```bash
python scripts/audit_github_merge_gate.py --repo daniedorfling18-maker/Claude
```

It writes `outputs/performance/independent_merge_gate.json`, which WO-68 consumes for WO-67 P4. A
missing check, offline runner, unsuccessful newest PR run, missing latest-push
review semantics, unresolved-conversation bypass, admin bypass, or direct push
path keeps `enforced=false`. A legacy branch-protection context named
`WO-69 guard and invariants` also keeps `enforced=false`: that API does not
bind a GitHub Actions check to `required-pr-gate.yml`, so another workflow
could publish the same check name.

## Private-plan boundary

GitHub returned HTTP 403 for branch protection and rulesets on this private Free-plan repository. Do
not make the repository public to work around that boundary. WO-100 therefore
adds `.github/workflows/independent-pr-merge.yml` as a documented fail-closed
fallback. Direct merges are prohibited: a second push-capable identity must
approve the exact current head and dispatch the workflow. It verifies the
newest exact-head gate, current-main ancestry, latest review state, and resolved
threads immediately before an atomic non-force update whose new commit has the
verified main revision as its only parent. If main advances during that final
window, the ref update is no longer a fast-forward and fails closed. The
original dispatcher and `github.triggering_actor` must both be current-head
approvers, so an unauthorized rerun cannot borrow the dispatcher's identity.
PRs that alter either merge workflow or either merge-control script are
rejected by this lane and require a separately reviewed control-plane bootstrap
until workflow-identity protection exists. Because the repository has only one
push-capable identity today, the lane is configured but BLOCKED.

On a successful atomic update, the workflow publishes the evaluator's complete
JSON result as the run-scoped artifact
`independent-main-acceptance-<run-id>/merge-attestation.json`. The artifact
binds the exact merge commit, verified head/tree, prior main parent, distinct
current-head approver/dispatcher, checks, and blockers. Deployment consumers
must verify both the originating workflow run and exact merge SHA; an artifact
name copied from another run is not acceptance.

After the owner upgrades the repository, establish a required-workflow ruleset
that binds enforcement to `.github/workflows/required-pr-gate.yml`. The
legacy policy can still be applied as review/direct-push hardening from the VPS,
but it is not proof of the required workflow and the audit deliberately remains
fail-closed:

```bash
python scripts/audit_github_merge_gate.py \
  --repo daniedorfling18-maker/Claude \
  --apply-protection
```

The legacy policy requires the named check context, an approval after the
latest push, stale-review dismissal, admin enforcement, no review bypasses, no
direct pushes, no force pushes, and no deletion. Those are useful controls but
do not supply workflow identity. Further live capital remains blocked until a
required-workflow ruleset is independently verified or the exact-head
second-identity process becomes operational; a same-named green context alone
is never sufficient.
