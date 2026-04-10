"""Async SQLite: schema init, migrations, and all query functions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

from config import DB_PATH

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     INTEGER PRIMARY KEY,
    faceit_nickname TEXT    NOT NULL,
    faceit_player_id TEXT   NOT NULL,
    registered_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    watching        INTEGER DEFAULT 0,
    last_match_id   TEXT    DEFAULT NULL
);
"""

_SCHEMA_ELO_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS elo_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id  INTEGER NOT NULL,
    elo          INTEGER NOT NULL,
    level        INTEGER NOT NULL,
    recorded_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);
"""

_SCHEMA_FSM = """
CREATE TABLE IF NOT EXISTS fsm_state (
    storage_key TEXT PRIMARY KEY,
    state TEXT,
    data TEXT NOT NULL DEFAULT '{}'
);
"""

_SCHEMA_REFERRALS = """
CREATE TABLE IF NOT EXISTS referrals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id   INTEGER NOT NULL,
    referred_id   INTEGER NOT NULL UNIQUE,
    credited_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (referrer_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
    FOREIGN KEY (referred_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_elo_snapshots_tid ON elo_snapshots(telegram_id, recorded_at);",
    "CREATE INDEX IF NOT EXISTS idx_users_watching ON users(watching);",
    "CREATE INDEX IF NOT EXISTS idx_users_player_id ON users(faceit_player_id);",
    "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);",
]

# Columns added after initial deploy; ALTER TABLE is idempotent via try/except.
_MIGRATIONS = [
    ("ALTER TABLE users ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP", None),
    ("ALTER TABLE users ADD COLUMN watching INTEGER DEFAULT 0", None),
    ("ALTER TABLE users ADD COLUMN last_match_id TEXT DEFAULT NULL", None),
]


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

async def init_db(db_path: str = DB_PATH) -> None:
    # Parent dir and writability are validated in config._compute_db_path(); do not mkdir here
    # (avoids PermissionError on /data when env points at Docker-only paths).
    async with aiosqlite.connect(db_path) as db:
        # WAL mode gives better concurrent read performance with aiosqlite.
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(_SCHEMA_USERS)
        await db.execute(_SCHEMA_ELO_SNAPSHOTS)
        await db.execute(_SCHEMA_FSM)
        await db.execute(_SCHEMA_REFERRALS)
        for idx_sql in _INDEXES:
            await db.execute(idx_sql)
        # Safe migrations for existing databases
        for sql, _ in _MIGRATIONS:
            try:
                await db.execute(sql)
            except sqlite3.OperationalError:
                pass  # duplicate column / already migrated
        await db.commit()


# ---------------------------------------------------------------------------
# User queries
# ---------------------------------------------------------------------------

async def list_all_registered_users(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """All linked accounts (for /leaderboard)."""
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT telegram_id, faceit_nickname, faceit_player_id FROM users "
        "ORDER BY faceit_nickname COLLATE NOCASE"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_telegram_ids_by_faceit_player_id(
    db: aiosqlite.Connection, faceit_player_id: str
) -> list[int]:
    """Reverse lookup: Telegram accounts linked to a FACEIT player (for admin / leaderboards)."""
    async with db.execute(
        "SELECT telegram_id FROM users WHERE faceit_player_id = ?",
        (faceit_player_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


async def get_user(db: aiosqlite.Connection, telegram_id: int) -> dict[str, Any] | None:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_user(
    db: aiosqlite.Connection,
    telegram_id: int,
    faceit_nickname: str,
    faceit_player_id: str,
) -> None:
    """Idempotent link: one row per Telegram user (ON CONFLICT UPDATE). Serialized with IMMEDIATE."""
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """
            INSERT INTO users (telegram_id, faceit_nickname, faceit_player_id,
                               registered_at, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                faceit_nickname  = excluded.faceit_nickname,
                faceit_player_id = excluded.faceit_player_id,
                updated_at       = CURRENT_TIMESTAMP
            """,
            (telegram_id, faceit_nickname, faceit_player_id),
        )
    except Exception:
        await db.rollback()
        raise
    await db.commit()


async def delete_user(db: aiosqlite.Connection, telegram_id: int) -> bool:
    async with db.execute(
        "SELECT 1 FROM users WHERE telegram_id = ? LIMIT 1",
        (telegram_id,),
    ) as cur:
        exists = await cur.fetchone() is not None
    if not exists:
        return False
    await db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Watch / alert queries
# ---------------------------------------------------------------------------

async def set_watching(
    db: aiosqlite.Connection, telegram_id: int, enabled: bool
) -> None:
    await db.execute(
        "UPDATE users SET watching = ? WHERE telegram_id = ?",
        (1 if enabled else 0, telegram_id),
    )
    await db.commit()


async def update_last_match_id(
    db: aiosqlite.Connection, telegram_id: int, match_id: str
) -> None:
    await db.execute(
        "UPDATE users SET last_match_id = ? WHERE telegram_id = ?",
        (match_id, telegram_id),
    )
    await db.commit()


async def get_watching_users(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT telegram_id, faceit_player_id, faceit_nickname, last_match_id "
        "FROM users WHERE watching = 1"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# ELO snapshot queries
# ---------------------------------------------------------------------------

async def record_elo_snapshot(
    db: aiosqlite.Connection, telegram_id: int, elo: int, level: int
) -> None:
    """Insert a snapshot only if ELO changed since the last recorded value."""
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT elo FROM elo_snapshots WHERE telegram_id = ? "
        "ORDER BY recorded_at DESC LIMIT 1",
        (telegram_id,),
    ) as cur:
        row = await cur.fetchone()
    if row and int(row["elo"]) == elo:
        return  # No change — skip
    await db.execute(
        "INSERT INTO elo_snapshots (telegram_id, elo, level) VALUES (?, ?, ?)",
        (telegram_id, elo, level),
    )
    await db.commit()


async def get_elo_snapshots(
    db: aiosqlite.Connection, telegram_id: int, limit: int = 14
) -> list[dict[str, Any]]:
    """Return the *limit* most recent ELO snapshots, oldest first."""
    db.row_factory = aiosqlite.Row
    async with db.execute(
        """
        SELECT elo, level, recorded_at FROM elo_snapshots
        WHERE telegram_id = ?
        ORDER BY recorded_at DESC
        LIMIT ?
        """,
        (telegram_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    # Return oldest-first for chart display
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Referral queries
# ---------------------------------------------------------------------------

async def has_been_referred(db: aiosqlite.Connection, referred_id: int) -> bool:
    """True if this user has already been credited as a referral."""
    async with db.execute(
        "SELECT 1 FROM referrals WHERE referred_id = ? LIMIT 1", (referred_id,)
    ) as cur:
        return await cur.fetchone() is not None


async def add_referral(
    db: aiosqlite.Connection, referrer_id: int, referred_id: int
) -> bool:
    """Record a referral. Returns True if successfully inserted, False if already exists."""
    try:
        await db.execute(
            "INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
            (referrer_id, referred_id),
        )
        await db.commit()
        return True
    except Exception:
        return False  # UNIQUE constraint — already recorded


async def get_referral_count(db: aiosqlite.Connection, referrer_id: int) -> int:
    """Number of users this person has successfully referred."""
    async with db.execute(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (referrer_id,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def get_referral_stats(
    db: aiosqlite.Connection, referrer_id: int
) -> dict[str, Any]:
    """Count of referrals + timestamp of the most recent one."""
    db.row_factory = aiosqlite.Row
    async with db.execute(
        """
        SELECT COUNT(*) AS total, MAX(credited_at) AS last_at
        FROM referrals WHERE referrer_id = ?
        """,
        (referrer_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {"total": 0, "last_at": None}
    return {"total": int(row["total"]), "last_at": row["last_at"]}
