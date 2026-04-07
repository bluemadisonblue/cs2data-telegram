"""Register the / command list shown in Telegram (Discoverability)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)


async def register_bot_commands(bot: Bot) -> None:
    """Expose core commands in the Telegram menu (⋯ or / in the input)."""
    commands = [
        BotCommand(command="start", description="Welcome & main menu"),
        BotCommand(command="help", description="All commands"),
        BotCommand(command="about", description="Version & data source"),
        BotCommand(command="register", description="Link your FACEIT account"),
        BotCommand(command="unlink", description="Remove FACEIT link"),
        BotCommand(command="profile", description="Profile card & avatar"),
        BotCommand(command="stats", description="CS2 stats dashboard"),
        BotCommand(command="matches", description="Recent match history"),
        BotCommand(command="match", description="Scoreboard by match ID"),
        BotCommand(command="rank", description="ELO & level progress"),
        BotCommand(command="compare", description="You vs one player"),
        BotCommand(command="party", description="Compare 2–6 players at once"),
        BotCommand(command="leaderboard", description="Registered users by ELO"),
        BotCommand(command="maps", description="Map mix & per-map stats"),
        BotCommand(command="trend", description="ELO history chart"),
        BotCommand(command="watch", description="New-match notifications"),
    ]
    await bot.set_my_commands(commands)
    names = ", ".join(c.command for c in commands)
    logger.info("set_my_commands OK (%d): %s", len(commands), names)
