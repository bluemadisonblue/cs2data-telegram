"""Async SQLite: schema init and user queries."""

from __future__ import annotations

import aiosqlite
from typing import Any

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    faceit_nickname TEXT NOT NULL,
    faceit_player_id TEXT NOT NULL,
    registered_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db(db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(SCHEMA)
        await db.commit()


async def get_user(db: aiosqlite.Connection, telegram_id: int) -> dict[str, Any] | None:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT telegram_id, faceit_nickname, faceit_player_id, registered_at FROM users WHERE telegram_id = ?",
        (telegram_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


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


async def upsert_user(
    db: aiosqlite.Connection,
    telegram_id: int,
    faceit_nickname: str,
    faceit_player_id: str,
) -> None:
    await db.execute(
        """
        INSERT INTO users (telegram_id, faceit_nickname, faceit_player_id)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            faceit_nickname = excluded.faceit_nickname,
            faceit_player_id = excluded.faceit_player_id,
            registered_at = CURRENT_TIMESTAMP
        """,
        (telegram_id, faceit_nickname, faceit_player_id),
    )
    await db.commit()
