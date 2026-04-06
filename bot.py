"""Entry point: polling, dispatcher, middlewares, shared aiohttp session."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

import database
from config import BOT_TOKEN, FACEIT_API_KEY
from faceit_api import FaceitAPI
from handlers import setup_routers
from middlewares.db_middleware import DbMiddleware


class FaceitInjectMiddleware(BaseMiddleware):
    """Injects the shared FaceitAPI client (no per-request overhead)."""

    def __init__(self, faceit: FaceitAPI) -> None:
        self._faceit = faceit

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["faceit"] = self._faceit
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not BOT_TOKEN or not FACEIT_API_KEY:
        raise SystemExit("Set BOT_TOKEN and FACEIT_API_KEY in .env")

    await database.init_db()

    async with aiohttp.ClientSession() as http:
        faceit = FaceitAPI(http, FACEIT_API_KEY)
        bot = Bot(
            token=BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher(storage=MemoryStorage())
        dp.update.middleware(FaceitInjectMiddleware(faceit))
        dp.update.middleware(DbMiddleware())
        dp.include_router(setup_routers())
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
