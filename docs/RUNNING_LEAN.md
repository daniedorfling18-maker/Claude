# Running lean — keeping memory off 99%

Two things eat the RAM: **Docker containers** (up to ~35 are defined across the compose files) and
**Codex** (a separate heavyweight agent). With no caps, a few stacks plus Codex peg an 8–16 GB box.
This guide keeps a useful subset running inside a fixed memory budget.

## 1. Every container now has a memory cap

Each service caps at `PM_MEM_LIMIT` (default **512m**, set in `.env`). So your Docker budget is simply:

```
RAM used by Docker  ≈  (number of containers you run)  ×  PM_MEM_LIMIT
```

Per stack at the default cap:

| Stack | Services | At 512m |
|---|---:|---:|
| `docker-compose.live.yml` | 3 | ~1.5 GB |
| `docker-compose.monitor.yml` | 2 | ~1.0 GB |
| `docker-compose.polymarket-collector.yml` | 1 | ~0.5 GB |
| `docker-compose.polymarket-fixed.yml` | 5 | ~2.5 GB |
| **`docker-compose.polymarket-wide-raw.yml`** | **20** | **~10 GB ← do not run whole** |
| `docker-compose.yml` | 4 | ~2.0 GB |

The 20-service `wide-raw` stack is broad data collection across every category — it is the usual
culprit. **Don't run it whole.** Run one stack (the duplicate-writer rule already says so), and lower
`PM_MEM_LIMIT` (e.g. `320m`) in `.env` if you want to pack more containers — at the cost of OOM-kill
risk on the pandas-heavy services (`pm-*-ml`, the mispricing bot).

## 2. Stop what you're not using

```powershell
docker ps                              # see what's actually up
docker stats --no-stream               # per-container memory right now
docker compose -f docker-compose.polymarket-wide-raw.yml down   # the big one
docker stop $(docker ps -q)            # or stop everything, then bring up one stack
```

Then bring up exactly one capped stack, e.g. the live watch stack:

```powershell
docker compose -f docker-compose.live.yml up -d --build
docker stats --no-stream               # confirm it sits near 3 × PM_MEM_LIMIT
```

## 3. The lowest-memory option: the paper bot needs no Docker at all

The crypto/paper bot you're implementing is **Docker-free by design**
(`scripts/run_polymarket_local_live_loop.py` — "local-first and Docker-free"). It's one Python
process plus a tiny dashboard server. If Docker memory is the problem, just run it directly:

```powershell
python scripts\run_polymarket_local_live_loop.py --config polymarket_predictive_config.example.yaml --max-assets 60
```

Keep `--max-assets` small (40–60) to bound the websocket/feature set. This is a few hundred MB total
versus dozens of containers. It still respects the kill switch, readiness, and P&L pause controls.

## 4. Let the engine back off under pressure

`runtime_resource_guard` in the config already skips heavy work when memory is high. If you keep
hitting the ceiling, lower the threshold so it backs off earlier:

```yaml
runtime_resource_guard:
  max_memory_percent: 85      # was 92; lower = the bot yields RAM sooner
  degraded_max_websocket_assets: 60
```

Note this throttles the **bot's own work** — it cannot shrink Docker or Codex, so steps 1–3 are the
real levers.

## 5. Codex — the leanest setup

Codex is a separate process tree; its memory is on top of everything above (the dashboard even
borrows Codex's bundled Node runtime). To keep it small:

- **Run one Codex session at a time.** Each open session/terminal is its own memory; close the ones
  you're not actively using.
- **Stop background Codex agents.** The `codex/*` branches suggest background runs — terminate any
  you're not watching (Task Manager → end the stray `node`/`codex` trees, or exit those terminals).
- **Don't run Codex and a Docker stack at full tilt simultaneously on a tight box.** A practical
  rhythm: use Codex to make changes, stop it, then run the bot/stack. Or run the Docker-free local
  bot (step 3) while Codex is open — that pairing is light.
- **Trim Codex's own load** where configurable (smaller model/context, fewer concurrent tools); avoid
  pointing it at the whole repo for large indexing passes while the bot is running.

## Quick recipe (tight laptop)

1. `docker stop $(docker ps -q)` — clear Docker.
2. Close extra Codex sessions; keep one (or none).
3. `python scripts\run_polymarket_local_live_loop.py --config polymarket_predictive_config.example.yaml --max-assets 50`
4. Watch Task Manager / `docker stats`. If you want containers too, bring up **one** capped stack only.
