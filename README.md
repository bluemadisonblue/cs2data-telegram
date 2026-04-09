# CS2 FACEIT Telegram bot

Telegram bot for **Counter-Strike 2** stats on **FACEIT**: link your account, view profile, rank, matches, maps, party compare, leaderboard, match alerts, and inline search.

Stack: **Python 3.12**, [aiogram](https://docs.aiogram.dev/) v3, **aiohttp**, **SQLite** (users, FSM, ELO history, watch state).

## Requirements

- Python 3.12+
- [FACEIT Data API](https://docs.faceit.com/docs/data-api/data) key
- Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick start (local)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -r requirements.txt
cp .env.example .env            # edit: BOT_TOKEN, FACEIT_API_KEY
python bot.py
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Telegram bot token |
| `FACEIT_API_KEY` | Yes | FACEIT Data API key |
| `DB_PATH` | No | SQLite path. Default: `bot_data.db` next to the app. |
| `LOG_UPDATES` | No | If `1` / `true` (default), log each update to logger `bot.requests` (kind, user_id, command/callback snippet). Set `0` to disable. |
| `SENTRY_DSN` | No | If set, uncaught handler errors are reported to [Sentry](https://sentry.io/). See `DEPLOY.md`. |
| `SENTRY_ENVIRONMENT` | No | Sentry environment label (default: `production`). |
| `SENTRY_TRACES_SAMPLE_RATE` | No | `0`–`1` for performance tracing (default: `0`). |
| `FACEIT_CIRCUIT_FAILURE_THRESHOLD` | No | After this many consecutive failed FACEIT request cycles, pause calls briefly (default `4`; `0` disables). |
| `FACEIT_CIRCUIT_OPEN_SEC` | No | Circuit-open duration in seconds (default `60`). |

Tunable constants (cooldowns, cache size, watch interval, etc.) live in `config.py`.

## Docker

**Smoke test**

```bash
docker build -t cs2-faceit-bot .
docker run --rm -e BOT_TOKEN=... -e FACEIT_API_KEY=... cs2-faceit-bot
```

**Persistent SQLite** (recommended for a VPS / Droplet): use the bundled Compose file — it sets `DB_PATH=/data/bot_data.db` and a named volume.

```bash
cp .env.example .env   # fill secrets
docker compose up -d --build
docker compose logs -f
```

**Backups and restore** (volume, `docker compose cp`, `sqlite3 .backup`): see [DEPLOY.md](DEPLOY.md).

## DigitalOcean App Platform

- Run as a **Worker** (long polling; no HTTP port). This repo includes **`.do/app.yaml`** — adjust `github.repo` / `branch`, then create or update the app from the spec.
- Set **secrets** in the dashboard: `BOT_TOKEN`, `FACEIT_API_KEY`. Optional: `PYTHONUNBUFFERED=1`.
- **Environment scope:** each variable must be available at **Run Time**. If a secret is **Build Time** only, the worker starts without it and exits immediately (“Non-Zero Exit Code”).
- **`DB_PATH`:** omit it on App Platform, or use a **relative** name such as `bot_data.db`. Do **not** set `DB_PATH=/data/bot_data.db` unless that path exists and is writable (Compose on a Droplet creates `/data` via a volume; the buildpack worker does not). If the directory cannot be created, the app falls back to `bot_data.db` next to the code and logs a warning.
- **SQLite on App Platform** is on an **ephemeral** filesystem: data is lost on redeploy. For durable registrations and history, use **Docker Compose on a Droplet** (above) or an external database (would require code changes).

**Build (Python buildpack):** **`.python-version`** (`3.12`) tells the buildpack the major line; DigitalOcean’s mirror may not yet host the *newest* patch (e.g. 3.12.13), which causes a **404** on install. This repo also includes **`runtime.txt`** (`python-3.12.8`) so the buildpack requests a patch that is available on the DO CDN. If a future deploy fails with “Unable to download/install Python”, bump `runtime.txt` to a patch listed in the [buildpack docs](https://do.co/apps-buildpack-python) or switch the component to build from the **`Dockerfile`** (official `python:3.12-slim` image, no DO Python tarball).

**Procfile:** only `process: command` lines — no comment lines containing `:` (the parser treats the first `:` as the separator).

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## MCP server (Claude Desktop / Claude Code)

`faceit_mcp_server.py` exposes FACEIT CS2 data as an [MCP](https://modelcontextprotocol.io/) server so any compatible client (Claude Desktop, Claude Code) can query it conversationally.

**Tools available**

| Tool | Description |
|------|-------------|
| `get_player_stats` | ELO, level, region, lifetime K/D, HS%, win rate, streaks |
| `get_match_history` | Last N matches — map, W/L, K/D, kills, HS%, K/R |
| `compare_players` | Side-by-side stats for 2–6 FACEIT nicknames |
| `get_leaderboard` | All bot-registered users ranked by live ELO |
| `get_elo_trend` | Stored ELO snapshots for a registered nickname |

**Setup (Claude Desktop)**

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "faceit-cs2": {
      "command": "python",
      "args": ["C:/full/path/to/CS2DATA/faceit_mcp_server.py"],
      "env": { "FACEIT_API_KEY": "your_key_here" }
    }
  }
}
```

Or omit `env` and keep `FACEIT_API_KEY` in the `.env` file next to the server.

**Setup (Claude Code)**

```bash
claude mcp add faceit-cs2 -- python C:/full/path/to/CS2DATA/faceit_mcp_server.py
```

Then set `FACEIT_API_KEY` in the project's `.env`.

**Example prompts once connected**

- *"What are s1mple's lifetime stats?"*
- *"Compare zywoo, niko, and device side by side"*
- *"Show me the leaderboard for our group"*
- *"How has my ELO changed over the last month?"* (requires bot registration)

## Project layout

| Path | Role |
|------|------|
| `bot.py` | Entry: polling, middlewares, background watch loop |
| `config.py` | Env + constants |
| `database.py` | SQLite schema and queries |
| `faceit_api.py` | FACEIT Data API client (cache, retries, circuit breaker) |
| `faceit_messages.py` | Shared user-facing HTML for FACEIT errors / empty states |
| `faceit_mcp_server.py` | MCP server for Claude Desktop / Claude Code |
| `fsm_storage.py` | FSM persistence in SQLite |
| `scripts/backup_sqlite.py` | Offline-safe SQLite backup (cron-friendly) |
| `handlers/` | Command and callback handlers |
| `keyboards/` | Inline keyboards |
| `middlewares/` | Per-update DB connection |
| `tests/` | Pytest |

## License

Use and modify as you wish for your own deployment; ensure you comply with [FACEIT](https://www.faceit.com/) and Telegram API terms.
