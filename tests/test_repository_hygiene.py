"""Repository hygiene: the front page, the document classification, the archive.

Registered by WO-168. Every assertion here reads files committed to this
repository. Nothing starts an engine, contacts a venue, or reads a runtime
artifact, so the suite is offline and hermetic.

The classification below is the single place the document partition is written
down. `README.md` prints the canonical rows and each class's count, and test 1
asserts those printed counts equal the counts here, so the front page cannot
drift from this dictionary without a test failing.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ARCHIVED = (
    "POLYMARKET_ACTUARIAL_GRADE_GAP_ASSESSMENT_20260628.md",
    "POLYMARKET_ENGINE_APPLY_NOTES.md",
    "POLYMARKET_LIVE_LEARNING_SYSTEM_DESIGN.md",
    "POLYMARKET_MISPRICING_BOT.md",
    "POLYMARKET_PAPER_PROFIT_AUDIT.md",
    "POLYMARKET_PREDICTIVE_POWER_ROADMAP.md",
    "POLYMARKET_STRATEGY_V2.md",
    "POLYMARKET_STRATEGY_V2_QUICKSTART.md",
    "POLYMARKET_VPS_DOCKER_DRY_RUN.md",
    "VPS_DOCKER_DRY_RUN_MONITOR.md",
    "VPS_RESTART_FORENSICS_2026-07-12.md",
    "VENTURE_THESIS.md",
    "LIVE_DUTCH_ARB_DOCKER.md",
    "POLYMARKET_RESOLUTION_COLLECTOR.md",
    "polymarket_overnight_governance_20260625.md",
)

ARCHIVE_REASONS: dict[str, str] = {
    "POLYMARKET_ACTUARIAL_GRADE_GAP_ASSESSMENT_20260628.md": "dated snapshot",
    "POLYMARKET_ENGINE_APPLY_NOTES.md": "legacy local design; VPS-only rule",
    "POLYMARKET_LIVE_LEARNING_SYSTEM_DESIGN.md": "legacy local design; VPS-only rule",
    "POLYMARKET_MISPRICING_BOT.md": "legacy local design; VPS-only rule",
    "POLYMARKET_PAPER_PROFIT_AUDIT.md": "superseded by `docs/POLYMARKET_QUANT_MODE_CHARTER.md`",
    "POLYMARKET_PREDICTIVE_POWER_ROADMAP.md": "superseded by `docs/POLYMARKET_EDGE_STRATEGY_RESET.md`",
    "POLYMARKET_STRATEGY_V2.md": "superseded by `docs/POLYMARKET_EDGE_STRATEGY_RESET.md`",
    "POLYMARKET_STRATEGY_V2_QUICKSTART.md": "superseded by `docs/POLYMARKET_EDGE_STRATEGY_RESET.md`",
    "POLYMARKET_VPS_DOCKER_DRY_RUN.md": "superseded by `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`",
    "VPS_DOCKER_DRY_RUN_MONITOR.md": "superseded by `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`",
    "VPS_RESTART_FORENSICS_2026-07-12.md": "dated snapshot",
    "VENTURE_THESIS.md": "dated snapshot",
    "LIVE_DUTCH_ARB_DOCKER.md": "legacy local design; VPS-only rule",
    "POLYMARKET_RESOLUTION_COLLECTOR.md": (
        "superseded by `src/polymarket_predictive_engine/resolution_collector.py`"
    ),
    "polymarket_overnight_governance_20260625.md": "dated snapshot",
}

CLASSIFICATION: dict[str, tuple[str, ...]] = {
    # 17 rows; 13 of them under docs/. The other four are the two front doors
    # and the two source paths, which the partition sum does not count.
    "canonical": (
        "README.md",
        "docs/EVIDENCE_STATE_2026-09-13.md",
        "AGENTS.md",
        "docs/ENGINEERING_STANDARDS.md",
        "docs/EXPERIMENT_REGISTRY.md",
        "docs/POLYMARKET_CODEX_WORK_ORDERS.md",
        "docs/POLYMARKET_QUANT_MODE_CHARTER.md",
        "docs/POLYMARKET_QUANT_TRADING_CONTRACT.md",
        "docs/OPERATING_STATE.md",
        "docs/ORACLE_VPS_SETUP.md",
        "docs/POLYMARKET_DOCKER_SAFETY_AUDIT.md",
        "docs/POLYMARKET_EDGE_STRATEGY_RESET.md",
        "docs/VPS_OUTAGE_2026-08-21.md",
        "docs/POLYMARKET_SHARP_ANCHOR.md",
        "docs/SYSTEM_MAP.md",
        "src/polymarket_predictive_engine/cli.py",
        "src/superbru_score_engine",
    ),
    "owner-surface": (
        "docs/OWNER_AMENDMENT_MB1_TIER0_COVERAGE.md",
        "docs/OWNER_AMENDMENT_SHARP_LINKING_EVALUATOR.md",
        "docs/OWNER_DECISION_FUNDING_GOVERNANCE.md",
        "docs/KEY_CUSTODY_DESIGN_WO67_P5.md",
        "docs/OWNER_CHECKS.md",
    ),
    "draft template, unsigned, not in force": ("docs/DRAFT_OWNER_AMENDMENT_WO67.md",),
    "referenced by code or tests": (
        "docs/A1_WITHDRAWAL_AND_EXIT_RAIL_RUNBOOK.md",
        "docs/EXECUTOR_SUB_ACCOUNT_AND_CREDENTIAL_DRILL.md",
        "docs/HUMAN_STAGE1_OPERATOR_RUNBOOK.md",
        "docs/MICRO_DRILL_RUNBOOK.md",
        "docs/POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md",
        "docs/POLYMARKET_API_ASSIMILATION.md",
        "docs/POLYMARKET_DATA_QUALITY_STANDARD.md",
        "docs/POLYMARKET_LIVE_TRADING_APPROVAL_CHECKLIST.md",
        "docs/POLYMARKET_MODEL_VALIDATION_STANDARD.md",
        "docs/POLYMARKET_PIPELINE_MAP.md",
        "docs/POLYMARKET_RISK_CONTROL_STANDARD.md",
        "docs/RESTORE.md",
    ),
    "retired in place with a loud notice": (
        "docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md",
        "docs/POLYMARKET_RESEARCH_README.md",
        "docs/POLYMARKET_PAPER_TRADING_LOOP.md",
        "docs/RUNNING_LEAN.md",
        "docs/POLYMARKET_RUNTIME_CONTEXT_20260628.md",
        "docs/POLYMARKET_CURRENT_STATE.md",
    ),
    "kept by cross-reference": (
        "docs/DRAFT_RISK_PREMIUM_HYPOTHESES.md",
        "docs/EXECUTOR_LIVE_OPS_CONTROL_PLANE.md",
        "docs/EXECUTOR_REPLAY_CERTIFICATION.md",
        "docs/MAKER_PICKOFF_SCALING_EXPERIMENT.md",
        "docs/QUANT_CURRICULUM.md",
        "docs/MARKET_MAKING_MODELS_RESEARCH.md",
        "docs/POLYMARKET_STRATEGY_OPTIONS.md",
        "docs/WO69_CI_ENFORCEMENT.md",
        "docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md",
        "docs/ACTUARIAL_AUDIT_PREDICTIVE_VALUE.md",
    ),
    "SuperBru ancillary": (
        "docs/backtest_validation.md",
        "docs/chaser_points_inference.md",
        "docs/leader_defence_workflow.md",
        "docs/predictive_value_controls.md",
        "docs/round_summary_behaviour.md",
        "docs/smartbet_grid_calibration.md",
        "docs/superbru-automation-context.md",
        "docs/validation_layer.md",
        "docs/SUPERBRU_CLV_VS_CLOSE_EXPERIMENT.md",
    ),
    "incident records": ("docs/incidents/2026-07-13-wo73-append-only-ledger-migration.md",),
    "archived": tuple(f"docs/archive/{name}" for name in ARCHIVED),
}

# One referrer each, never a disjunction: a disjunction cannot be a literal
# dictionary value, and a document kept because *something* names it is a
# document nothing is accountable for.
CROSS_REFERENCE_REFERRERS: dict[str, str] = {
    "docs/DRAFT_RISK_PREMIUM_HYPOTHESES.md": "docs/POLYMARKET_CODEX_WORK_ORDERS.md",
    "docs/EXECUTOR_LIVE_OPS_CONTROL_PLANE.md": "docs/OPERATING_STATE.md",
    "docs/EXECUTOR_REPLAY_CERTIFICATION.md": "docs/EXECUTOR_SUB_ACCOUNT_AND_CREDENTIAL_DRILL.md",
    "docs/MAKER_PICKOFF_SCALING_EXPERIMENT.md": "docs/POLYMARKET_QUANT_MODE_CHARTER.md",
    "docs/QUANT_CURRICULUM.md": "docs/POLYMARKET_QUANT_MODE_CHARTER.md",
    "docs/MARKET_MAKING_MODELS_RESEARCH.md": "docs/POLYMARKET_CODEX_WORK_ORDERS.md",
    "docs/POLYMARKET_STRATEGY_OPTIONS.md": "docs/POLYMARKET_CODEX_WORK_ORDERS.md",
    "docs/WO69_CI_ENFORCEMENT.md": "docs/POLYMARKET_CODEX_WORK_ORDERS.md",
    "docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md": "docs/ORACLE_VPS_SETUP.md",
    "docs/ACTUARIAL_AUDIT_PREDICTIVE_VALUE.md": "docs/POLYMARKET_RESEARCH_README.md",
}

# The five documents docs/OPERATING_STATE.md:62-76 names authoritative.
AUTHORITATIVE = (
    "README.md",
    "AGENTS.md",
    "docs/OPERATING_STATE.md",
    "docs/EXPERIMENT_REGISTRY.md",
    "docs/SYSTEM_MAP.md",
)

README_HEADINGS = (
    "What this repository is for",
    "Generated state",
    "State of the evidence",
    "Retracted figures",
    "Supported workflows",
    "Known limitations",
    "Governance in one paragraph",
    "Documents",
)

OBJECTIVE_PARAGRAPH = (
    "This repository is a research engine for one question: can a pre-registered, "
    "fail-closed, paper-only process tell a profitable, executable strategy apart "
    "from a historical premium, an accounting error, an overfit result, or "
    "insufficient evidence? A defensible negative result is a successful outcome."
)

VPS_ONLY_SENTENCES = (
    "Production and verification are VPS-only.",
    "Do not run Python engines, tests, Docker, dashboards, scheduled tasks, "
    "collectors, model training, brokers, or watchdogs on the local workstation.",
    "Local work is limited to code inspection and editing, Git/GitHub operations, "
    "and SSH control.",
)

CLOSE_OUT_GUARD_SENTENCE = (
    "These two selectors landed on `main` with #455, and their results are not "
    "verification of record until that pull request's required gate runs."
)

RETRACTED_TOKENS = ("63.62", "60.60", "3.02/day")

# Registered by test 1 and, until a third review, never implemented: the one
# defect item 1 names — `README.md:19` printed `http://129.151.178.42:8765/` at
# `6cf7fc6`, against AGENTS.md's Tailscale-only rule — could be re-introduced
# with the whole suite green.
_BARE_IPV4_URL = re.compile(r"https?://\d{1,3}(?:\.\d{1,3}){3}")

EVIDENCE_STATE = "docs/EVIDENCE_STATE_2026-09-13.md"

EVIDENCE_ROWS: tuple[tuple[str, str], ...] = (
    ("H1 sharp-anchor maker carry", "modeled"),
    ("H2 dutch-book", "unread"),
    ("H3 smart-flow", "untested"),
    ("The legacy $100/month verdict engine", "terminal"),
    ("Perpetual funding carry", "historical-class diagnostic"),
    ("Variance risk premium", "existence observed"),
)

GUARDED_ROWS = (
    "The legacy $100/month verdict engine",
    "Perpetual funding carry",
    "Variance risk premium",
)

CLOSE_OUT_GUARD_CLAUSE = (
    "recorded in the charter and the register by #455, and not verification of "
    "record until that pull request's required gate runs"
)

# A numeric token is a maximal run of non-space, non-pipe characters containing
# a digit. Tokens, not substrings: `55` is a substring of `#455`, so deleting the
# permitted literals from a row is fail-open in one direction and self-defeating
# in the other. Removing them in the order the register lists them turns `#455`
# into `#4`; removing them longest-first turns a forbidden `$55` into a bare `$`,
# which no pattern catches. Extraction has neither failure.
_NUMERIC_TOKEN = re.compile(r"[^\s|]*\d[^\s|]*")
# Wrappers may be trimmed from either end; sentence punctuation only from the
# trailing end. Trimming punctuation from the LEADING end is what a second
# review caught: str.strip is symmetric, so `.55` and `-55` both reduced to the
# permitted `55`, and a bare decimal and a negative walked through the guard.
_TOKEN_WRAP = "`*_()[]{}<>\"'\u201c\u201d\u2018\u2019"
_TOKEN_TAIL = ",.;:!?\u2014\u2013-"


def _numeric_tokens(row: str) -> list[str]:
    tokens = []
    for raw in _NUMERIC_TOKEN.findall(row):
        token = raw.strip(_TOKEN_WRAP).rstrip(_TOKEN_TAIL)
        if token.endswith("'s"):
            token = token[:-2]
        token = token.strip(_TOKEN_WRAP).rstrip(_TOKEN_TAIL)
        if token and any(char.isdigit() for char in token):
            tokens.append(token)
    return tokens


FORBIDDEN_ON_GUARDED_ROWS = ("annualised_simple", "carry_v0.json")

# Every row's numeric content is pinned exactly, as a MULTISET, and every row has
# its own set — including the three guarded ones.
#
# Two reviews were needed to get here. The second found the H1 row unguarded on
# the registered ground that WO-173 "has no results to print", while printing the
# five figures WO-173's own entry enumerates — the favourable modelled reading
# A11 names as this work order's favourable direction. The third found that
# permit-LISTS still let a row move a permitted figure anywhere inside itself:
# "+$1.68/day against the $3.33/day target" became "$3.33/day against the
# $3.33/day target, met", raising the modelled carry to the target with the suite
# green; and that mapping the guarded rows to one shared set let the Variance
# risk premium row borrow `$100/month`, `55` and `2026-08-19` from the other two.
# Counting each token fixes both: a row's figures are exactly these, in exactly
# these quantities.
ROW_NUMERIC_TOKENS: dict[str, dict[str, int]] = {
    "H1 sharp-anchor maker carry": {
        "H1": 1,
        "+$1.68/day": 1,
        "$3.33/day": 1,
        "3": 1,
        "77.5%": 1,
        "$0": 1,
        "WO-173": 1,
        "#455": 1,
    },
    "H2 dutch-book": {
        "H2": 2,
        "300": 1,
        "0": 2,
        "67": 1,
        "0.0": 1,
        "2026-08-21": 1,
        "docs/VPS_OUTAGE_2026-08-21.md": 2,
        "outputs/h2_dutch/h2_evaluation.json": 1,
    },
    "H3 smart-flow": {"H3": 1, "0": 1, "2026-07-17": 1},
    "The legacy $100/month verdict engine": {
        "$100/month": 1,
        "2026-08-19": 1,
        "\u22120.013943": 1,
        "55": 1,
        "WO-169": 1,
        "#455": 1,
    },
    "Perpetual funding carry": {
        "WO-166": 1,
        "WO-167": 2,
        "WO-170": 2,
        "#455": 3,
        "#454": 1,
    },
    "Variance risk premium": {"WO-166": 1, "WO-170": 1, "#455": 1, "#454": 1},
}

# The prose outside the table is pinned too: a second review printed a figure in
# the closing paragraph and the row-scoped rule never saw it.
PROSE_PERMITTED_NUMERIC = frozenset(
    {"2026-09-13", "2026-08-21", "fcebaa2", "#455", "#454", "WO-166"}
)

ROOT_MARKDOWN = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "BACKTESTING_README.md",
    "DAILY_AUTOMATION_README.md",
    "README_DOCKER_MONITOR.md",
)

# Historical register text this work order may not edit, naming a file that no
# longer exists. Test 2 asserts this is the ONLY unresolved reference, so a new
# stale reference fails rather than joining a growing allowlist.
REFERENCE_ALLOWLIST = (("docs/POLYMARKET_CODEX_WORK_ORDERS.md", "docs/VPS_PAPER_RUNBOOK.md"),)

MINIMUM_REFERENCE_TOKENS = 100

# The optional group takes a markdown link title: `](path "title")`. Without it
# a stale target with a title was invisible to the scan.
_LINK = re.compile(r"\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)")
_BACKTICK = re.compile(r"`([^`]*)`")
_DOC_TOKEN = re.compile(r"\Adocs/\S*\.md\Z")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _readme_sections(text: str) -> dict[str, str]:
    """Split README into its `## ` sections, on the unnormalised text.

    A heading line is one whose first three characters are exactly "## ". Each
    region is returned unnormalised so callers can collapse whitespace within a
    region without ever collapsing across a heading boundary.
    """
    lines = text.splitlines()
    heads = [i for i, line in enumerate(lines) if line[:3] == "## "]
    sections: dict[str, str] = {}
    for n, start in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        sections[lines[start][3:].strip()] = "\n".join(lines[start + 1 : end])
    return sections


def _table_rows(text: str) -> list[list[str]]:
    """Data rows of the first markdown table: header and separator dropped."""
    rows: list[list[str]] = []
    seen_header = False
    seen_separator = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not seen_header:
            seen_header = True
            continue
        if not seen_separator:
            seen_separator = True
            continue
        rows.append(cells)
    return rows


def test_readme_carries_the_retraction_and_the_evidence_pointer() -> None:
    text = _read("README.md")
    sections = _readme_sections(text)

    heading_lines = [line for line in text.splitlines() if line[:3] == "## "]
    assert len(heading_lines) == 8, heading_lines
    assert [line[3:].strip() for line in heading_lines] == list(README_HEADINGS)

    # Each retracted figure appears exactly once on the page, and that single
    # occurrence is inside the Retracted figures region. One escape, stated: a
    # figure re-entered in a different spelling ("$63.62 per day") is not caught,
    # because only these three literal tokens are pinned.
    retracted_region = sections["Retracted figures"]
    for token in RETRACTED_TOKENS:
        assert text.count(token) == 1, token
        assert token in retracted_region, token

    # The objective paragraph is the section's FIRST paragraph and is pinned
    # exactly, not by containment: a second review appended "The maker lane is
    # now profitable." to it and a containment assertion passed.
    first_paragraph = _collapse(
        sections["What this repository is for"].strip().split("\n\n", 1)[0]
    )
    assert first_paragraph == OBJECTIVE_PARAGRAPH, first_paragraph

    workflows_region = _collapse(sections["Supported workflows"])
    for sentence in VPS_ONLY_SENTENCES:
        assert sentence in workflows_region, sentence

    # The two work-order selectors may be printed only under the guard.
    for selector in ("verify-results --work-order WO-167", "verify-results --work-order WO-170"):
        if selector in _collapse(text):
            assert CLOSE_OUT_GUARD_SENTENCE in workflows_region, selector

    # Front-door drift: all eight registered patterns must be absent, and the
    # generated-state pointer present. Imported rather than restated so the two
    # can never diverge.
    import sys

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from polymarket_predictive_engine.operating_state import (  # noqa: PLC0415
        _DRIFT_PATTERNS,
        front_door_drift_violations,
    )

    assert len(_DRIFT_PATTERNS) == 8
    # Scoped to README.md: AGENTS.md is outside this work order's touch list and
    # must not be able to fail this test.
    violations = front_door_drift_violations(readme_text=text, agents_text=_read("AGENTS.md"))
    assert [v for v in violations if v.startswith("README.md:")] == [], violations
    assert "performance/operating_state.md" in text
    assert not _BARE_IPV4_URL.search(text), _BARE_IPV4_URL.search(text)

    # The link must be in the section that promises it. A second review moved it
    # to the Documents table, leaving "State of the evidence" naming a document
    # it did not link, and a whole-file assertion passed.
    assert f"]({EVIDENCE_STATE})" in sections["State of the evidence"]

    # Every class line's printed count equals this file's count for that class.
    documents_region = sections["Documents"]
    # findall, not search, and the set must be exactly the nine: a third review
    # added a tenth class line carrying an unpinned number, and a duplicate
    # `canonical` line whose count was 86 away from the dictionary. Both passed
    # a first-match check, which is the channel this work order exists to close.
    printed = re.findall(r"- \*\*([^*]+)\*\* — (\d+) under `docs/`", documents_region)
    assert len(printed) == 9, printed
    assert {name for name, _ in printed} == set(CLASSIFICATION), (
        {name for name, _ in printed} ^ set(CLASSIFICATION)
    )
    for name, printed_count in printed:
        under_docs = sum(1 for path in CLASSIFICATION[name] if path.startswith("docs/"))
        assert int(printed_count) == under_docs, (name, printed_count, under_docs)


def _reference_scan_files() -> list[Path]:
    files = [REPO_ROOT / name for name in ROOT_MARKDOWN if (REPO_ROOT / name).is_file()]
    for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
        if "archive" in path.relative_to(REPO_ROOT).parts:
            continue
        files.append(path)
    return files


def test_every_reference_to_a_doc_resolves() -> None:
    visited = 0
    misses: list[tuple[str, str]] = []
    for path in _reference_scan_files():
        rel = str(path.relative_to(REPO_ROOT))
        # A file that cannot be read fails this test rather than being skipped:
        # a silent skip is fail-open for exactly the stale reference this exists
        # to catch.
        text = path.read_text(encoding="utf-8")

        tokens: list[str] = []
        for match in _LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            tokens.append(target)
        for match in _BACKTICK.finditer(text):
            span = match.group(1)
            # A span is a token only when its ENTIRE content is a docs/ path
            # ending in .md. A line or range suffix ("....md:62-76") is a
            # citation, not a path.
            if _DOC_TOKEN.match(span):
                tokens.append(span)

        for token in tokens:
            # Skip rule: a token containing *, < or > is a form, not a path.
            # Load-bearing, not cosmetic: the register writes `docs/**/*.md` and
            # `](<target>)`, and without this the test would fail on a correct
            # tree — the register defeating its own test.
            if any(char in token for char in "*<>"):
                continue
            visited += 1
            target = token.split("#", 1)[0]
            if not target:
                continue
            if not (REPO_ROOT / target).exists():
                misses.append((rel, token))

    assert visited >= MINIMUM_REFERENCE_TOKENS, visited
    assert sorted(set(misses)) == sorted(REFERENCE_ALLOWLIST), misses


def test_archive_readme_lists_every_archived_file() -> None:
    archive = REPO_ROOT / "docs" / "archive"
    index = archive / "README.md"
    text = index.read_text(encoding="utf-8")

    # Item 2 requires the file to record the command used. A substring check,
    # stated as such rather than dressed up as a sentence check.
    assert "git mv" in text

    on_disk = sorted(p.name for p in archive.iterdir() if p.name != "README.md")
    assert on_disk, "no archived files found"

    rows = _table_rows(text)
    assert len(rows) == len(on_disk) == 15, (len(rows), len(on_disk))

    listed = []
    for cells in rows:
        assert len(cells) == 3, cells
        name, reason, replacement = (c.strip("` ") for c in cells)
        listed.append(name)
        assert (archive / name).is_file(), name
        if reason.startswith("superseded by "):
            target = reason[len("superseded by ") :].strip("` ")
            assert (REPO_ROOT / target).exists(), target
            assert replacement == target, (replacement, target)
        else:
            assert reason in ("dated snapshot", "legacy local design; VPS-only rule"), reason
            assert replacement == "none", replacement

    assert sorted(listed) == on_disk, (sorted(listed), on_disk)

    # The mapping, not only its shape. Downgrading a `superseded by <path>` row
    # to `dated snapshot`/`none` erases the supersession, and shape alone
    # accepts it.
    assert set(ARCHIVE_REASONS) == set(on_disk), set(ARCHIVE_REASONS) ^ set(on_disk)
    for cells in rows:
        name = cells[0].strip("` ")
        assert cells[1].strip() == ARCHIVE_REASONS[name], (name, cells[1])


def test_docs_classification_is_an_exhaustive_partition() -> None:
    for path in CLASSIFICATION["archived"]:
        assert (REPO_ROOT / path).is_file(), path

    # docs/archive/README.md is excluded from the classified domain: it is the
    # index of the archive, not a document in it. Excluding it is what stops the
    # index having to classify itself.
    domain = {
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "docs").rglob("*.md")
        if "archive" not in p.relative_to(REPO_ROOT).parts
    }
    assert len(domain) == 57, len(domain)

    non_archived_classes = {k: v for k, v in CLASSIFICATION.items() if k != "archived"}
    seen: dict[str, str] = {}
    for name, members in non_archived_classes.items():
        for path in members:
            if not path.startswith("docs/"):
                continue
            assert path not in seen, (path, seen.get(path), name)
            seen[path] = name
    assert seen.keys() == domain, (domain - seen.keys(), seen.keys() - domain)

    sizes = {
        name: sum(1 for p in members if p.startswith("docs/"))
        for name, members in CLASSIFICATION.items()
    }
    assert sizes == {
        "canonical": 13,
        "owner-surface": 5,
        "draft template, unsigned, not in force": 1,
        "referenced by code or tests": 12,
        "retired in place with a loud notice": 6,
        "kept by cross-reference": 10,
        "SuperBru ancillary": 9,
        "incident records": 1,
        "archived": 15,
    }, sizes
    assert sum(sizes.values()) == 72, sum(sizes.values())

    canonical = set(CLASSIFICATION["canonical"])
    for path in AUTHORITATIVE:
        assert path in canonical, path

    readme = _read("README.md")
    documents = _readme_sections(readme)["Documents"]
    canonical_table = _table_rows(documents)
    # The table, not the page: a second review deleted the table and re-emitted
    # the 17 links as one prose line, and a whole-file assertion passed.
    assert len(canonical_table) == 17, len(canonical_table)
    table_text = "\n".join(" ".join(cells) for cells in canonical_table)
    for path in CLASSIFICATION["canonical"]:
        assert f"]({path})" in table_text, path

    # Every row of AGENTS.md's "Stable references" table is canonical.
    agents = _read("AGENTS.md")
    stable = agents.split("## Stable references", 1)[1]
    stable = stable.split("\n## ", 1)[0]
    stable_paths = {
        span
        for span in _BACKTICK.findall(stable)
        if span.startswith(("docs/", "src/"))
    }
    assert stable_paths, "no stable-reference rows found"
    for path in sorted(stable_paths):
        assert path in canonical, path

    assert set(CROSS_REFERENCE_REFERRERS) == set(CLASSIFICATION["kept by cross-reference"])
    for path, referrer in CROSS_REFERENCE_REFERRERS.items():
        referrer_path = REPO_ROOT / referrer
        assert referrer_path.is_file(), referrer
        name = Path(path).name
        assert name in referrer_path.read_text(encoding="utf-8"), (referrer, name)


def test_evidence_state_rows_carry_a_class_and_respect_the_close_out_guard() -> None:
    path = REPO_ROOT / EVIDENCE_STATE
    assert path.is_file(), EVIDENCE_STATE
    text = path.read_text(encoding="utf-8")

    header = text.split("| line |", 1)[0]
    assert "2026-08-21" in header
    # The whole sentence, not the fragment: a header reading "this is not
    # current state" would satisfy the fragment and say something else.
    assert "These are readings of the 2026-08-21 snapshot, not current state." in _collapse(header)

    rows = _table_rows(text)
    assert len(rows) == 6, len(rows)
    by_line = {cells[0].strip("` "): cells for cells in rows}
    assert len(by_line) == 6, by_line.keys()

    for line, evidence_class in EVIDENCE_ROWS:
        assert line in by_line, line
        cells = by_line[line]
        # Paired with its own class, not merely present somewhere in the table:
        # a swap between two rows is an upward relabel and membership alone
        # would pass it.
        assert cells[1].strip("` ") == evidence_class, (line, cells[1], evidence_class)

    # Every row is pinned, as a multiset, to its own registered tokens.
    assert set(ROW_NUMERIC_TOKENS) == {line for line, _ in EVIDENCE_ROWS}
    for line, cells in by_line.items():
        assert len(cells) == 5, (line, len(cells))
        counted = Counter(_numeric_tokens(_collapse(" ".join(cells))))
        assert counted == Counter(ROW_NUMERIC_TOKENS[line]), (
            line,
            counted - Counter(ROW_NUMERIC_TOKENS[line]),
            Counter(ROW_NUMERIC_TOKENS[line]) - counted,
        )

    # The header and separator rows are inside the guard too. A third review put
    # a retracted figure and a favourable net into the header, where _table_rows
    # drops the first two pipe-lines and the prose scan skips every pipe-line, so
    # nothing read them at all.
    pipe_lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    assert len(pipe_lines) >= 2, pipe_lines
    header_tokens = _numeric_tokens(_collapse(" ".join(pipe_lines[:2])))
    assert header_tokens == [], header_tokens

    checked = 0
    for line in GUARDED_ROWS:
        collapsed = _collapse(" ".join(by_line[line]))
        assert CLOSE_OUT_GUARD_CLAUSE in collapsed, line
        for forbidden in FORBIDDEN_ON_GUARDED_ROWS:
            assert forbidden not in collapsed, (line, forbidden)
        checked += 1
    assert checked == 3, checked

    # The prose outside the table is pinned too: a second review printed a
    # figure in the closing paragraph, where the row-scoped rule never looked.
    prose = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("|"))
    prose_tokens = _numeric_tokens(_collapse(prose))
    assert prose_tokens, "no prose tokens found"
    unpermitted = [tok for tok in prose_tokens if tok not in PROSE_PERMITTED_NUMERIC]
    assert not unpermitted, unpermitted
