# CLAUDE.md

Follow [`AGENTS.md`](AGENTS.md). It is the single source of truth shared by all
coding agents.

The operating rule not to miss is **VPS only**: the local workstation is for
code/Git/GitHub/SSH control, while engines, tests, Docker, dashboards, schedulers,
collectors, brokers, models, and watchdogs run only on the VPS. Keep the system
paper/dry-run and do not add or enable a live order path or relax any gate.
