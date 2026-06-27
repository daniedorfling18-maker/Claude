# CLAUDE.md

**Follow [`AGENTS.md`](AGENTS.md)** — it is the single source of truth for how to run and work in
this repo, shared by all coding agents. Don't duplicate its rules here; read it.

The one rule not to miss: this repo is **local-first**. Develop and run with plain Python
(`python scripts/run_polymarket_local_live_loop.py`, the `polymarket-engine` CLI, `pytest`).
**Docker is for VPS / 24-7 / live deployment only — do not spin it up for local work.** Everything
stays paper/dry-run; never add a live order path or relax the live-trading gates (see `AGENTS.md`).
