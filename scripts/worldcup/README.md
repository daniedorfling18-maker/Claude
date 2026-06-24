# World Cup / Polymarket validation scripts

Ad-hoc, run-once pipeline scripts from the 2026 World Cup Polymarket validation
effort. They were previously dumped at the repository root; they live here to keep
the root clean. They are **not** part of the installed `polymarket_predictive_engine`
package and are **not** collected by the test suite.

Run them from the repository root (they use working-directory-relative paths), e.g.:

```bash
python scripts/worldcup/build_worldcup_model_readiness_gate.py
```

Rough groupings:

- `discover_* / find_* / probe_* / inspect_*` — locate World Cup fixtures, markets
  and Gamma/CLOB token ids.
- `collect_* / extract_* / refresh_*` — pull market snapshots, CLOB prices and Gamma
  status for those markets.
- `build_* / inventory_* / join_* / seed_* / settle_* / set_*` — assemble validation
  sets, paper-trade candidates/ledgers, readiness gates and SuperBru pick boards.
- `audit_* / diagnose_*` — one-off data-quality and condition-context checks.

Throwaway / already-applied (kept only for provenance; do not re-run):

- `find_hanging_test*.py` — pytest hang-bisection helpers.
- `patch_*.py` — one-shot in-place source patchers whose edits are already committed
  (they reference their targets by the old root-relative path).
