#!/usr/bin/env python3
"""Consistent SQLite backup using the online backup API (safe if the bot is running).

Usage:
  python scripts/backup_sqlite.py [SOURCE_DB] [DEST_DIR]

Defaults: SOURCE_DB from env DB_PATH or ./bot_data.db, DEST_DIR ./backups

Example (cron on host where the DB file is visible):
  0 3 * * * cd /opt/cs2data && .venv/bin/python scripts/backup_sqlite.py /data/bot_data.db /var/backups/cs2data
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    default_src = os.getenv("DB_PATH", str(root / "bot_data.db"))
    src = Path(sys.argv[1] if len(sys.argv) > 1 else default_src).expanduser()
    dest_dir = Path(sys.argv[2] if len(sys.argv) > 2 else root / "backups").expanduser()

    if not src.is_file():
        print(f"error: source database not found: {src}", file=sys.stderr)
        return 1

    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"bot_data-{stamp}.db"

    try:
        with sqlite3.connect(str(src)) as src_conn, sqlite3.connect(str(dest)) as dest_conn:
            src_conn.backup(dest_conn)
    except sqlite3.Error as exc:
        print(f"error: backup failed: {exc}", file=sys.stderr)
        return 1

    print(f"ok: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
