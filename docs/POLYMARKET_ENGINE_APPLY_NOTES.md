# Engine apply notes: retired

The original patch-copy/install note described repository assembly before the
engine was integrated. It is no longer an installation or test procedure.

The package, CLI entry point, configuration example and ignore rules are now
tracked in the repository. Do not copy an external tree over the checkout or
run the project locally. Changes follow a scoped work order and PR; tests run in
the isolated ARM64/Python 3.11 VPS gate, and production deploys through the
guarded workflow in `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md`.
