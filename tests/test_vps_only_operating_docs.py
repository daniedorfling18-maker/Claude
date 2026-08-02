from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_front_doors_are_vps_only() -> None:
    agents = _text("AGENTS.md")
    claude = _text("CLAUDE.md")
    readme = _text("README.md")
    current_state = _text("docs/POLYMARKET_CURRENT_STATE.md")

    for name, text in {
        "AGENTS.md": agents,
        "CLAUDE.md": claude,
        "README.md": readme,
    }.items():
        assert "VPS only" in text or "VPS-only" in text, name
        assert "local-first" not in text.lower(), name

    assert "Do not start any of the following on the local workstation" in agents
    assert "docker-compose.vps-paper.yml" in agents
    assert "outputs/performance/operating_state.json" in agents
    assert "manually maintained snapshot formerly stored here was retired" in current_state


def test_wo135_sandbox_test_carve_out_stays_narrow() -> None:
    # WO-135. Dispatched builds were arriving with every test marked "not run
    # locally", correctly citing the VPS-only rule's ban on running test suites off
    # the VPS. The ban is narrowed for a hermetic suite in an ephemeral agent
    # sandbox - and this test exists so the carve-out cannot later be widened into
    # "agents may run production paths", which is the whole thing the rule protects.
    agents = _text("AGENTS.md")

    # The prohibition itself survives verbatim.
    assert "Do not start any of the following on the local workstation" in agents
    assert "- Python engines, collectors, model training, test suites, brokers, or watchdogs;" in agents

    # The carve-out is present, scoped to a sandbox, and mandatory.
    assert "Amendment, 2026-07-27 — the offline suite in an agent sandbox" in agents
    assert "ephemeral, network-isolated sandbox" in agents
    assert "MUST run the offline `pytest` suite" in agents

    # Everything with runtime side effects stays banned outside the VPS.
    for runtime_path in (
        "engines, collectors, model training, brokers, watchdogs, schedulers, dashboards",
        "Docker/Compose",
        "any run against a real `paths.output_root`",
        "contacts a live venue, wallet, or paid API",
    ):
        assert runtime_path in agents, runtime_path

    # And the required gate remains the only thing that verifies a change.
    assert "A sandbox run is never verification of record." in agents
    assert "self-hosted ARM64 required" in agents
    assert "neither substitutes for it nor licenses a merge" in agents


def test_wo133_two_deploy_paths_are_named_and_path_a_stays_required() -> None:
    # WO-133. Before this, the runbook's manual route bypassed the workflow's
    # guard order, so "Actions is down" meant an undocumented ad-hoc pull. Both
    # paths are now named - and Path B must never read as a peer of Path A,
    # because only Path A binds the SHA to an independent review.
    agents = _text("AGENTS.md")

    assert "Two paths are permitted" in agents
    assert "REQUIRED whenever" in agents
    assert "scripts/deploy_vps_paper_manual.sh" in agents
    assert "attestation_verified: false" in agents
    assert "never writes an attestation" in agents
    assert "ad-hoc pull/rebuild remains forbidden" in agents


def test_wo145_policy_vs_capability_amendment_is_additive_and_names_the_invocation_form() -> None:
    # WO-145 / §145.1. The amendment is additive and dated - it must not
    # rewrite the AGENTS.md:85-90 sentences the assertions above pin, and it
    # must record: (1) the policy-vs-capability distinction (no eligible
    # independent reviewer can exist, which is a POLICY limit, not a
    # capability one); (2) that the Path B permission is permanent, not
    # sunset-lapsing; (3) the literal, non-substituted Path B invocation form
    # §145.1(a1b) registers, because the bare form refuses after the :99
    # fallback is deleted.
    agents = _text("AGENTS.md")

    assert "Amendment, 2026-08-01/2026-08-02 (WO-145 / §145.1)" in agents
    assert "no eligible reviewer exists" in agents
    assert "POLICY** limit, not a capability one" in agents
    assert "merge_independently_reviewed_pr.py:340-347" in agents
    assert "permanent, by owner direction (2026-08-01)" in agents
    assert "not a\n  temporary allowance pending a lapsing condition" in agents

    assert "deploy_vps_paper_dispatch.yml" in agents
    assert "trigger_mechanism" in agents
    assert "workflow_dispatch` | `push` | `vps_shell" in agents

    assert "PM_DEPLOY_TARGET_SHA=<40-hex commit sha> scripts/deploy_vps_paper_manual.sh" in agents
    assert "A LITERAL forty-hex SHA, read from the merged pull request on GitHub" in agents
    assert "NOT\n  a shell substitution" in agents
    assert "$(git rev-parse origin/main)" in agents
    assert "is withdrawn as defective" in agents

    # The existing Path A/Path B sentences (:85-107, pinned above) survive
    # byte-for-byte - this amendment only adds text after them.
    assert "Path A — the `Deploy Polymarket VPS Paper` workflow. REQUIRED whenever" in agents
    assert "Path B — `scripts/deploy_vps_paper_manual.sh`, for when Path A is not" in agents


def test_legacy_local_runbooks_are_loudly_archived() -> None:
    expected_markers = {
        "docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md": "Retired local runbook",
        "docs/POLYMARKET_RESEARCH_README.md": "Historical snapshot",
        "docs/POLYMARKET_PAPER_TRADING_LOOP.md": "Retired local runbook",
        "docs/RUNNING_LEAN.md": "Historical local-capacity guide",
        "docs/POLYMARKET_RUNTIME_CONTEXT_20260628.md": "Archived context",
    }

    for path, marker in expected_markers.items():
        first_lines = "\n".join(_text(path).splitlines()[:10])
        assert marker in first_lines, path
        assert "AGENTS.md" in first_lines, path


def test_work_order_queue_distinguishes_non_buildable_states() -> None:
    work_orders = _text("docs/POLYMARKET_CODEX_WORK_ORDERS.md")

    assert "Current queue for Codex (reconciled 2026-07-19)" in work_orders
    assert "remains CLOSED and WO-67 remains BLOCKED" in work_orders
    assert "Each scope requires its own PR" in work_orders
    assert "**WO-100:** rebuild closed PR #243 from current main" in work_orders
    assert "**WO-101:** rebuild closed PR #242 from current main" in work_orders
    assert "fix exact-token identity, sharp-source" in work_orders
    assert "implement an actual tested rollback" in work_orders
    assert "authenticated HTTPS or a private VPN" in work_orders
    assert "Next buildable: WO-92" not in work_orders
    wo92_heading = work_orders.split("## WO-92", 1)[1].splitlines()[0]
    assert "done (2026-07-15, PR #234)" in wo92_heading
    assert "ENGINEERING_STANDARDS.md" in work_orders
    assert "ROOT CAUSE CORRECTED by line audit" in work_orders
    assert "WO-33/34/35 remain governed by WO-101's leakage" in work_orders
    assert "WO-48, WO-67, WO-73 item 4, and WO-75 item 2 remain" in work_orders
    assert "WO-70 and WO-72 remain deferred" in work_orders
    assert "WO-76 remains registration-only" in work_orders
    assert "Queue now: **WO-80**" not in work_orders

    wo91 = work_orders.split("## WO-91", 1)[1].split("## WO-90", 1)[0]
    assert "Fail-safe direction:" in wo91
    assert "Day-after check:" in wo91
    assert "recorded CLOB `/prices-history` payload" in wo91


def test_maker_module_does_not_write_owner_authorization_claims() -> None:
    source = _text("src/polymarket_predictive_engine/maker_carry_study.py")

    assert "authorization = owner merge" not in source
    assert "owner merge of this frozen-surface PR" not in source


def test_wo132_register_headings_carry_auditable_pr_attribution() -> None:
    # WO-132. GitHub's squash-merge record names the PR that landed, but never
    # who clicked merge - so a register heading that claims WHO merged a PR is
    # an unprovable, agent-written authorization claim. A completed ("done"/
    # "DONE") heading may cite the PR number; it may never name an actor.
    work_orders = _text("docs/POLYMARKET_CODEX_WORK_ORDERS.md")

    headings = [line for line in work_orders.splitlines() if line.startswith("## WO-")]
    assert headings, "expected at least one work-order heading"

    banned_phrases = ("owner merged", "merged by the owner", "orchestrator merged")
    for heading in headings:
        lowered = heading.lower()
        for phrase in banned_phrases:
            assert phrase not in lowered, heading

    for heading in headings:
        if "done (" in heading:
            assert "PR #" in heading, heading


def test_wo132_unresolved_review_threads_block_merge() -> None:
    # WO-132. 49 unresolved inline review threads sat on green, already-merged
    # PRs - proving the required gate answers "did the tests pass", not "was
    # review answered". This is now a standing merge precondition, not just a
    # one-time triage note.
    agents = _text("AGENTS.md")

    assert "Amendment, 2026-08-01 — unresolved review threads block merge" in agents
    assert "must not be merged" in agents
    assert "counts as UNRESOLVED and blocks merge" in agents
    assert "demonstrably superseded" in agents
    assert "does not replace the required PR gate" in agents
