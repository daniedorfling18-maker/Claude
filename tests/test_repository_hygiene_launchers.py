"""Launcher hygiene: every file under `scripts/` is named by something else.

Registered by WO-168a. Test 1 is not a one-off check on that cleanup; it is a
standing repository invariant, and `AGENTS.md`'s "Work-order and Git discipline"
section states it as one. Two things follow, registered rather than left to be
discovered:

* The invariant is weak evidence of life. A basename inside a comment satisfies
  it, two dead scripts naming each other survive the fixpoint permanently, and it
  proves nothing about reachability.
* It is a real constraint on future work. On the post-removal tree 38 of the 123
  survivors have exactly one referrer, so a pull request that adds a script
  before its caller, or that deletes a document or a test, can turn this suite
  red without touching `scripts/` at all.

Nothing here runs an engine, contacts a venue, or reads a runtime artifact.
"""

from __future__ import annotations

import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The scan roots of WO-168a item 1, literal and exhaustive. This list is the
# authority: `AGENTS.md` delegates the roots to this file by name, so narrowing
# one here narrows the registered rule. Test 1 asserts it against its own second
# copy below, so a narrowing takes two edits in the same file.
DIRECTORY_ROOTS = (
    "src",
    "scripts",
    "tests",
    ".github",
    "docs",
    "analysis",
    "research",
    "examples",
    "notebooks",
    "config",
    "calibration",
)
ROOT_FILE_PATTERNS = ("*.md", "*.yml", "*.yaml", "*.toml", "*.ini", "*.cmd", "Dockerfile*")

# `.claude/` is deliberately NOT a root: it is agent-configuration space that the
# lifecycle companion itself calls non-register-reviewed, so no file's survival
# may depend on it. `data/`, `inputs/` and `outputs/` are generated or input
# state; `.git/` and the tool caches are not source.

REMOVED_BY_WO168A = (
    "check_superbru_fixtures.py",
    "config_check.py",
    "install_daily_superbru_task.ps1",
    "install_polymarket_local_live_task.ps1",
    "log_strategy_v2_persistence.ps1",
    "predict_latest_cached.ps1",
    "run_daily_oddspedia_overlay.ps1",
    "run_daily_superbru_scheduled.ps1",
    "run_data_inventory_local.ps1",
    "run_defensive_model.py",
    "run_probability_converter_loop.sh",
    "wc_grid_validation.py",
    "audit_oddspedia_available_data_cdp_session.py",
    "audit_superbru_available_data_cdp_session.py",
    "build_data_inventory_summary.py",
    "scrape_oddspedia_cdp_session.py",
    "enhance_superbru_fixture_coverage.py",
    "filter_oddspedia_high_value_market_paths.py",
)

# Every script that survived the removal. Asserted present, so over-removal fails
# as loudly as under-removal: an empty fixpoint alone is satisfied by deleting
# everything. The count is a floor, not an equality, so a later work order may add
# a referenced script without editing this list; deleting any recorded survivor
# fails.
SURVIVING_SCRIPTS = (
    "archive_oddspedia_snapshot.py",
    "assert_fresh_superbru_refresh_inputs.py",
    "audit_github_merge_gate.py",
    "audit_polymarket_local_history.py",
    "audit_superbru_validation_data_freshness.py",
    "auto_pick_match_scoped.py",
    "auto_pick_match_scoped_smart_odds.py",
    "bootstrap_polymarket_vps_paper.sh",
    "build_daily_robust_card.py",
    "build_final_locked_picks.py",
    "build_live_chaser_profiles.py",
    "build_live_signal_backtest.py",
    "build_market_odds_validation.py",
    "build_oddspedia_match_urls_from_nuxt.py",
    "build_oddspedia_model_independence.py",
    "build_oddspedia_score_shape_features.py",
    "build_oddspedia_signal_archive.py",
    "build_oddspedia_superbru_ev.py",
    "build_oddspedia_synthetic_pool_crowding.py",
    "build_pick_validation_report.py",
    "build_predictions_from_locked_card.py",
    "build_repo_worldcup_winner_probabilities.py",
    "build_superbru_backtest_from_results.py",
    "build_superbru_pool_intelligence.py",
    "check_oddspedia_grid_quality.py",
    "check_polymarket_vps_paper.sh",
    "check_telemetry_credential_guard.py",
    "compare_locked_picks_to_oddspedia.py",
    "compare_oddspedia_movement.py",
    "configure_polymarket_dashboard_tailscale.sh",
    "convert_smartbet_grids_to_calibration.py",
    "deploy_vps_paper_manual.sh",
    "differentiation_overlay.py",
    "discover_oddspedia_match_urls.py",
    "discover_oddspedia_match_urls_curl.py",
    "ev_contrarian.py",
    "exact_chase.py",
    "executor_credential_fail_flat_stub.py",
    "export_research_ledgers.sh",
    "fetch_market_odds_theoddsapi.py",
    "fit_chaser_profiles_from_points.py",
    "fit_chaser_profiles_from_round_summary.py",
    "inspect_superbru_dom_ci.py",
    "install_polymarket_dashboard_task.ps1",
    "install_polymarket_paper_maintenance_task.ps1",
    "install_polymarket_shadow_research_task.ps1",
    "log_prediction_snapshots.py",
    "merge_independently_reviewed_pr.py",
    "normalise_old_oddspedia_grids.py",
    "notify_daily_superbru_action_items.py",
    "notify_score_changes.py",
    "polymarket_event_arb_pnl.py",
    "polymarket_goal_status.py",
    "polymarket_long_short_engine.py",
    "polymarket_market_making_eval.py",
    "polymarket_mispricing_bot.py",
    "polymarket_ml_collector.py",
    "preflight_vps_capacity.py",
    "push_vps_anchor.sh",
    "push_vps_archive.sh",
    "push_vps_telemetry.sh",
    "render_polymarket_dashboard.py",
    "restore_from_archive.sh",
    "rollback_vps_paper_deploy.py",
    "run_alpha_candidate_shadow_evidence.py",
    "run_calibration_diagnostics.py",
    "run_component_validation_rescore.py",
    "run_daily_robust_pipeline.py",
    "run_daily_superbru_local.ps1",
    "run_dutch_arb_monitor.sh",
    "run_final_leader_decision.py",
    "run_leaderboard_mc_stress.py",
    "run_leaderboard_monte_carlo.py",
    "run_live_mispricing_loop.sh",
    "run_long_short_loop.py",
    "run_long_short_loop.sh",
    "run_market_making_eval_loop.py",
    "run_oddspedia_pipeline.py",
    "run_polymarket_dashboard_server.ps1",
    "run_polymarket_liquidity_discovery.py",
    "run_polymarket_live_paper_loop.py",
    "run_polymarket_local_live_loop.py",
    "run_polymarket_opportunity_audit.ps1",
    "run_polymarket_opportunity_audit.py",
    "run_polymarket_paper_maintenance.ps1",
    "run_polymarket_pipeline.py",
    "run_polymarket_shadow_research_cycle.ps1",
    "run_polymarket_strategy_v2_anchored_edge.ps1",
    "run_polymarket_strategy_v2_anchored_edge.py",
    "run_polymarket_strategy_v2_cycle.ps1",
    "run_pregame_check.py",
    "run_pregame_watcher.py",
    "run_probability_converter_loop.py",
    "run_promoted_rule_shadow_scan.py",
    "run_sharp_anchor_loop.sh",
    "run_strategy_v2_cycle_scheduled_wrapper.ps1",
    "run_superbru_auto_pick_watchdog.sh",
    "run_vps_deploy_acceptance.sh",
    "run_vps_ops_scheduler.sh",
    "scrape_oddspedia_curl.py",
    "scrape_oddspedia_stealth.py",
    "scrape_superbru_leaderboard.py",
    "scrape_superbru_pool_cdp_session.py",
    "scrape_superbru_pool_picks_cdp_session.py",
    "scrape_superbru_results_cdp_session.py",
    "serve_polymarket_dashboard.js",
    "serve_polymarket_dashboard.py",
    "start_polymarket_dashboard.ps1",
    "start_polymarket_local_live.ps1",
    "submit_superbru_pick_cdp.py",
    "submit_superbru_pick_cdp_aliases.py",
    "superbru_clv_experiment.py",
    "team_name_aliases.py",
    "update_market_odds_history.py",
    "update_vps_checkout_preserving_runtime.py",
    "validate_dashboard_private_transport.py",
    "verify_executor_credential_fail_flat.py",
    "verify_independent_main_acceptance.py",
    "verify_validation_stack.py",
    "vps_diagnostic.sh",
    "wc_predictive_power_validation.py",
    "write_telemetry_export_manifest.py",
    "write_vps_telemetry_manifest.py",
)

# The only files under the roots that do not decode as UTF-8. A file that cannot
# be read is not a referrer, which is fail-open for a removal decision, so the set
# is pinned: a pull request adding a non-UTF-8 file — a raster diagram in `docs/`
# included — amends this literal in the same pull request.
UNDECODABLE_UNDER_ROOTS = (
    "research/premium_poc/data/deribit/BTC_funding_1h.csv.gz",
    "research/premium_poc/data/deribit/ETH_funding_1h.csv.gz",
)

# Registered floors. Pinned against their own second copies below, for the same
# reason the scan roots are: a line audit set each to 1 and the suite stayed
# green, and MINIMUM_REFERRERS_VISITED is the backstop that would catch a large
# root narrowing.
MINIMUM_SCRIPTS_VISITED = 100
MINIMUM_REFERRERS_VISITED = 400

AGENTS_PASSAGE = (
    "Every file under `scripts/`, at any depth, must be named by at least one "
    "**other** file under the repository's scan roots, as "
    "`tests/test_repository_hygiene_launchers.py` defines them \u2014 its basename "
    "appearing in that other file, or, for a `.py` script, an import of its stem "
    "\u2014 and neither that test file nor `docs/POLYMARKET_CODEX_WORK_ORDERS.md` "
    "counts as a referrer. A pull request that adds a script acquires its referrer "
    "in the same pull request; a pull request that removes a script's last referrer "
    "either keeps a referrer or removes the script. There is no allowlist."
)

AGENTS_CLOSING_PARAGRAPH = (
    "Legacy local launchers and runbooks remain only where another file under the "
    "repository's scan roots still references them, as the rule in \"Work-order and "
    "Git discipline\" defines them; the 18 unreferenced ones were removed under "
    "WO-168a and remain in Git history. Their presence is not permission to run them "
    "locally."
)

SUPERSEDED_CLOSING_CLAUSE = "remain only for repository history and regression coverage"

WORK_ORDER_SECTION_HEADING = "## Work-order and Git discipline"


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _blocks(text: str) -> list[str]:
    """Blank-line-separated blocks, each collapsed on its own.

    Collapsing the whole file at once destroys the paragraph and line-start
    structure two of test 2's checks need, and an uncollapsed literal check could
    never fire, because the sentences here are hard-wrapped in `AGENTS.md`.
    """
    return [_collapse(b) for b in re.split(r"\n\s*\n", text) if b.strip()]


def _git_tracked() -> list[str]:
    """Git-tracked paths only.

    A generated or gitignored file under a root is not a referrer, so the scan is
    reproducible from the commit rather than from a built working tree. A non-zero
    exit, a missing `git`, or an empty enumeration fails the test rather than
    skipping it.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    paths = [p for p in result.stdout.decode("utf-8").split("\0") if p]
    assert paths, "git ls-files returned nothing"
    return paths


def _under_roots(rel: str) -> bool:
    parts = rel.split("/")
    if "__pycache__" in parts:
        return False
    if len(parts) > 1:
        return parts[0] in DIRECTORY_ROOTS
    return any(fnmatch(rel, pattern) for pattern in ROOT_FILE_PATTERNS)


def _refers(text: str, script_rel: str) -> bool:
    """WO-168a's corrected matching rule.

    The basename as a substring, or — for a `.py` script — an import of its stem.
    An earlier draft said "the stem as a whole word", which is not an import:
    under it `config_check.py` read as referenced by an unrelated `config_check`
    subparser, and `check_superbru_fixtures.py` by a workflow FILENAME.
    """
    base = Path(script_rel).name
    if base in text:
        return True
    if base.endswith(".py"):
        stem = re.escape(base[:-3])
        if re.search(rf"^\s*from {stem} import", text, re.M):
            return True
        if re.search(rf"^\s*import {stem}\b", text, re.M):
            return True
        if re.search(rf"scripts\.{stem}\W", text):
            return True
    return False


def _exclusion_paths() -> tuple[str, ...]:
    """The two record-keeping files, parsed out of the registered `AGENTS.md`
    passage as this module records it.

    The parse is over the module constant, not the live file; identity with
    `AGENTS.md` is enforced transitively by test 2's third check, which asserts
    the passage appears there in full and exactly once.

    Each must name the removed launchers in order to record or assert their
    removal, so counting either as a referrer would blind the scan it is recorded
    in. Reading them from registered text means the exclusion set and the rule
    cannot be widened by one edit to this file.
    """
    spans = re.findall(r"`([^`]*)`", AGENTS_PASSAGE)
    return tuple(s for s in spans if "/" in s and s.endswith((".py", ".md")))


def test_every_script_is_referenced() -> None:
    tracked = _git_tracked()

    # The root list, asserted against its own second copy: `AGENTS.md` delegates
    # the roots to this file, so without this a single edit here would silently
    # narrow the registered rule.
    assert DIRECTORY_ROOTS == (
        "src",
        "scripts",
        "tests",
        ".github",
        "docs",
        "analysis",
        "research",
        "examples",
        "notebooks",
        "config",
        "calibration",
    )
    assert ROOT_FILE_PATTERNS == (
        "*.md",
        "*.yml",
        "*.yaml",
        "*.toml",
        "*.ini",
        "*.cmd",
        "Dockerfile*",
    )

    excluded = _exclusion_paths()
    assert len(excluded) == 2, excluded
    for path in excluded:
        assert (REPO_ROOT / path).is_file(), path
    own = str(Path(__file__).resolve().relative_to(REPO_ROOT))
    assert own in excluded, (own, excluded)

    scan_files = [rel for rel in tracked if _under_roots(rel)]
    scripts = [rel for rel in tracked if rel.split("/")[0] == "scripts" and "__pycache__" not in rel]
    assert MINIMUM_SCRIPTS_VISITED == 100
    assert MINIMUM_REFERRERS_VISITED == 400
    assert len(scripts) >= MINIMUM_SCRIPTS_VISITED, len(scripts)
    assert len(scan_files) >= MINIMUM_REFERRERS_VISITED, len(scan_files)

    texts: dict[str, str] = {}
    undecodable: list[str] = []
    for rel in scan_files:
        try:
            texts[rel] = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            undecodable.append(rel)
    assert tuple(sorted(undecodable)) == UNDECODABLE_UNDER_ROOTS, undecodable

    present = {Path(rel).name for rel in scripts}
    for name in REMOVED_BY_WO168A:
        assert name not in present, name
    for name in SURVIVING_SCRIPTS:
        assert name in present, name
    assert len(scripts) >= len(SURVIVING_SCRIPTS), (len(scripts), len(SURVIVING_SCRIPTS))

    # One referrer function, used by the fixpoint and by the probe below, so the
    # probe cannot be bypassed by editing the fixpoint alone.
    def referrers(script: str, gone: set[str]) -> list[str]:
        return [
            rel
            for rel in texts
            if rel != script and rel not in excluded and rel not in gone
            and _refers(texts[rel], script)
        ]

    # The same fixpoint item 1 computes, asserted empty: a newly orphaned launcher
    # fails here even if this work order's own removal was correct.
    removed: set[str] = set()
    while True:
        wave = [s for s in scripts if s not in removed and not referrers(s, removed)]
        if not wave:
            break
        removed.update(wave)
    assert removed == set(), sorted(removed)

    # A probe whose basename appears in this file and nowhere else in the tree.
    # With the exclusion applied it has no referrer; without it, this file is
    # one. So deleting the exclusion from `referrers` — which a line audit did,
    # leaving both tests green — fails here instead of passing silently. It
    # matters because this file names all 123 survivors, so an unexcluded test
    # file is a referrer for every script and the standing invariant becomes
    # satisfied by nothing. The name is deliberately not a real path.
    probe = "scripts/__exclusion_probe__.py"
    assert _refers(texts[own], probe), "the probe must be named by this file"
    assert referrers(probe, set()) == [], referrers(probe, set())

    # The exclusion set is load-bearing, and that it is APPLIED must be asserted
    # too. A line audit deleted `rel not in excluded` from the comprehension
    # above and both tests stayed green: this file names all 123 survivors, so
    # without the exclusion it becomes a referrer for every one of them and the
    # standing invariant is satisfied forever by nothing. That is the vacuity
    # the entry names and closes for the rename seam; this is the other seam.
    unexcluded = [
        script
        for script in scripts
        if not any(
            rel != script and _refers(texts[rel], script)
            for rel in texts
            if rel not in excluded
        )
    ]
    assert not unexcluded, unexcluded
    # A probe whose name appears in this file and nowhere else in the tree. With
    # the exclusion applied it has no referrer; without it, this file is one. So
    # deleting the exclusion from the comprehension above fails here instead of
    # passing silently. The name is deliberately not a real path.
    probe = "scripts/__exclusion_probe__.py"
    probe_referrers = [
        rel
        for rel in texts
        if rel != probe and rel not in excluded and _refers(texts[rel], probe)
    ]
    assert probe_referrers == [], probe_referrers
    assert _refers(texts[own], probe), "the probe must be named by this file"


def test_agents_md_carries_both_registered_amendments() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    blocks = _blocks(text)
    assert blocks, "AGENTS.md has no blocks"

    # 1. The closing paragraph, character for character and in full. Asserting
    # only that the amended sentence is present in the last block would leave
    # "Their presence is not permission to run them locally." pinned by nothing.
    assert blocks[-1] == AGENTS_CLOSING_PARAGRAPH, blocks[-1]

    # 2. The superseded clause is gone from the whole file, not merely from the
    # last block — which is what fails a builder who appends the new paragraph
    # without deleting the old one.
    for block in blocks:
        assert SUPERSEDED_CLOSING_CLAUSE not in block, block

    # 3. The three-sentence passage, in full and exactly once, inside its own
    # section. One escape, stated: the check is bounded to the section, so a
    # second copy placed outside it is not counted.
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == WORK_ORDER_SECTION_HEADING)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i][:4] == "### "),
        len(lines),
    )
    section = _collapse("\n".join(lines[start + 1 : end]))
    assert section.count(AGENTS_PASSAGE) == 1, section.count(AGENTS_PASSAGE)

    # 4. The two paths test 1 excludes are read out of that registered passage.
    # The natural shape rule "ends in .py or .md" returns three spans, so the
    # `/` requirement is load-bearing.
    spans = re.findall(r"`([^`]*)`", AGENTS_PASSAGE)
    assert len(spans) == 4, spans
    assert len([s for s in spans if s.endswith((".py", ".md"))]) == 3, spans
    assert _exclusion_paths() == (
        "tests/test_repository_hygiene_launchers.py",
        "docs/POLYMARKET_CODEX_WORK_ORDERS.md",
    )
