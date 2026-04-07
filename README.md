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

## Project layout

| Path | Role |
|------|------|
| `bot.py` | Entry: polling, middlewares, background watch loop |
| `config.py` | Env + constants |
| `database.py` | SQLite schema and queries |
| `faceit_api.py` | FACEIT Data API client (cache, retries) |
| `fsm_storage.py` | FSM persistence in SQLite |
| `handlers/` | Command and callback handlers |
| `keyboards/` | Inline keyboards |
| `middlewares/` | Per-update DB connection |
| `tests/` | Pytest |

## License

Use and modify as you wish for your own deployment; ensure you comply with [FACEIT](https://www.faceit.com/) and Telegram API terms.
