# Operating state control

Point-in-time operating status is generated from effective configuration and machine-readable evidence.
This tracked document intentionally contains no current status values.

Canonical outputs:

- `outputs/performance/operating_state.md` — operator-readable report.
- `outputs/performance/operating_state.json` — dashboard and automation source.
- Dashboard section **Canonical operating state** — a rendering of the same JSON.

Generate or refresh them with:

```bash
python -m polymarket_predictive_engine.cli operating-state --config polymarket_predictive_config.example.yaml
```

The daily VPS harvest runs the same command. The generator reads effective paper/live configuration,
the configured public wallet marker, governance authorization, paper activity, taker and maker verdicts,
WO-67 precondition evidence, and the host telemetry deployment manifest. Missing artifacts render as
`UNKNOWN`; prose is never used to fill a gap. The command is reporting-only and cannot invoke paper or
live trading.

README.md and AGENTS.md may point here or to the generated files, but must not restate dynamic values.
The drift test enforces that rule.
