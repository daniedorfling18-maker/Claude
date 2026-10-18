# Owner decisions pending

The operator loop appends here; the owner ticks items off. Each line is one
decision, the evidence in one line, and what a yes and a no each unblock. Open
items are `- [ ]`, decided ones `- [x]` with the decision and date appended.
Nothing here is authorization; it is the queue of things only the owner may
authorize.

- [ ] **The written Outcome-B decision: productize, shelve, or restart with fresh registration** — `docs/VENTURE_THESIS.md` binds this: terminal verdict NO on 2026-08-19, 30-day no-new-lanes window and 30-day customer-discovery window both elapsed with no decision recorded — yes to any branch gives the loop its rule 6; no decision keeps every build rule parked.
- [ ] **Merge or reject PR #456 (WO-168 + WO-168a)** — four S8 checks and two line audits, all findings closed; suite 2326 passed; red-team not run; required gate has never executed on it — merging lands the first work since 2026-09-13; rejecting returns it to the branch with the reasons recorded.
- [ ] **Unblock the required gate: split the offline suite onto a hosted runner, or restore the self-hosted one** — `required-pr-gate.yml` runs only on the ARM64 self-hosted runner, dead since 2026-08-23; the offline suite is pure Python and passes in a sandbox; this is a registered-surface change — yes unblocks all 9 open PRs; no leaves every merge on owner override.
- [ ] **Delete the 191 fully-landed branches** — tip equals the sha that merged, content is in `main`; the agent session's push right is scoped to its own branch and `push --delete` returns 403 — list and command in `.claude/skills/operator-loop/sediment_2026-10-18.md`; yes removes nothing that is not in `main`.
- [ ] **Decide the 59 sediment branches with unmerged commits** — 12 closed-never-merged, 32 never had a PR, 15 reused after their merge; listed in the same file — each is either a draft to revive, work to register, or history to drop; the loop may not delete any of them.
- [ ] **Close or revive the 8 pull requests open since August** — #416, #417 (2026-08-01), #446, #447, #449, #450, #451, #453; several self-titled HELD, DRAFT or NOT ADMISSIBLE — closing records why; reviving needs the gate first.
- [ ] **The VPS back for collection only** — H2's verdict artifact `outputs/h2_dutch/h2_evaluation.json` has never been read; H3's collector stopped on 2026-07-17; neither is a new lane; no order path, no paper loop — yes converts two unknowns to readings; no keeps the deadlock the brief names.
