# Deploy notes: SQLite persistence and backups

## Docker Compose (recommended for a VPS)

The bundled `docker-compose.yml` stores the database on a **named volume** so it survives container rebuilds and image updates:

- **Volume:** `faceit_bot_data` → mounted at `/data` in the container  
- **Database file:** `DB_PATH=/data/bot_data.db`

Do not remove the volume unless you intend to wipe user data.

### Backup the database

**1. Copy the file out of a running container**

```bash
docker compose cp bot:/data/bot_data.db ./bot_data.backup.db
```

**2. Copy from the volume (one-off container)**

```bash
docker run --rm -v cs2_faceit_bot_faceit_bot_data:/data -v "$(pwd)":/backup alpine \
  cp /data/bot_data.db /backup/bot_data.backup.db
```

Adjust the volume name if your Compose project name differs (`docker volume ls`).

**3. SQLite online backup (consistent snapshot)**

```bash
docker compose exec bot sh -c 'sqlite3 /data/bot_data.db ".backup /data/bot_data.backup.db"'
docker compose cp bot:/data/bot_data.backup.db ./bot_data.backup.db
```

Restore by stopping the bot, replacing `/data/bot_data.db` with your backup (or copying the backup file over it), then starting again.

### Automated daily backup (recommended)

Use the repo script with Python 3.12+ on a host that can **read the same DB file** the bot uses (bind-mount, or copy from the volume first).

```bash
# From the project root; optional: DB_PATH=/data/bot_data.db python scripts/backup_sqlite.py …
python scripts/backup_sqlite.py /path/to/bot_data.db /path/to/backups
```

**Cron** (example: 03:15 UTC daily, app deployed under `/opt/cs2data`, venv):

```cron
15 3 * * * cd /opt/cs2data && /opt/cs2data/.venv/bin/python scripts/backup_sqlite.py /data/bot_data.db /var/backups/cs2data
```

**Docker Compose** (DB only inside the container): run a scheduled job on the host that executes the `sqlite3 … ".backup …"` flow from the manual section above, or bind-mount `./data:/data` and point the script at `./data/bot_data.db` so the file is visible on the host.

Keep several dated files and prune old backups (e.g. `find … -mtime +14 -delete`) so the disk does not fill.

### FACEIT circuit breaker

After repeated failed FACEIT calls (after retries), the bot **pauses outbound requests** briefly so it does not hammer a struggling API. Tune with environment variables (optional):

- `FACEIT_CIRCUIT_FAILURE_THRESHOLD` — consecutive failed cycles before opening (default `4`; set `0` to disable).
- `FACEIT_CIRCUIT_OPEN_SEC` — how long to stay open (default `60`).

### Observability

- **Request logs:** logger `bot.requests` — one line per update with `kind`, `user_id`, and command/callback/query snippet. Disable with `LOG_UPDATES=0` if logs are too noisy.
- **Errors:** set `SENTRY_DSN` to send uncaught handler exceptions to Sentry. Optional: `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` (0–1, default `0`).
