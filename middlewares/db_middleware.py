"""Inject aiosqlite connection into handler data."""

from __future__ import annotations

import aiosqlite
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import DB_PATH


class DbMiddleware(BaseMiddleware):
    def __init__(self, db_path: str = DB_PATH) -> None:
        self._db_path = db_path

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            data["db"] = db
            return await handler(event, data)
