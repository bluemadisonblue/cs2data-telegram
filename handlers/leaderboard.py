"""Registered users ranked by live FACEIT ELO."""

from __future__ import annotations

import html as html_mod
import time

from aiogram import Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import database as dbmod
from config import COOLDOWN_SEC, LEADERBOARD_MAX_USERS, level_tier_emoji
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    extract_cs2_game,
)
from faceit_messages import html_faceit_transport_error
from keyboards.inline import with_navigation
from ui_text import bold, code, italic, section, sep

router = Router(name="leaderboard")

_last: dict[int, float] = {}


async def _cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last[user_id] = now
    return None


@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message, db, faceit) -> None:
    if msg := await _cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return

    users = await dbmod.list_all_registered_users(db)
    if not users:
        await message.answer(
            bold("No one is registered yet.") + f"\n{italic('Use /register to link your FACEIT account.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    cap = min(len(users), LEADERBOARD_MAX_USERS)
    users = users[:cap]

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer("⏳ Fetching ELO for registered players…")

    rows: list[tuple[int, str, int, str]] = []
    for u in users:
        pid = u["faceit_player_id"]
        nick_db = u.get("faceit_nickname") or "?"
        try:
            p = await faceit.get_player_by_id(pid)
        except FaceitNotFoundError:
            rows.append((0, nick_db, 0, "❔"))
            continue
        except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
            await loading.delete()
            await message.answer(
                html_faceit_transport_error(exc),
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return
        g = extract_cs2_game(p) or {}
        elo = int(g.get("faceit_elo") or 0)
        level = int(g.get("skill_level") or 0)
        tier = level_tier_emoji(level) if level else "❔"
        disp = str(p.get("nickname") or nick_db)
        rows.append((elo, disp, level, tier))

    await loading.delete()

    rows.sort(key=lambda r: (-r[0], r[1].lower()))

    lines = [
        section("🏅", "Leaderboard"),
        italic("Registered bot users · live FACEIT CS2 ELO"),
        italic(f"{len(rows)} accounts · max {LEADERBOARD_MAX_USERS} fetched per request"),
        sep(28),
    ]
    for rank, (elo_val, disp, lvl, tier) in enumerate(rows, start=1):
        med = "🥇" if rank == 1 else ("🥈" if rank == 2 else ("🥉" if rank == 3 else f"{rank}."))
        nick_e = html_mod.escape(disp[:22])
        lines.append(
            f"{med} <b>{nick_e}</b>  {tier} L{code(str(lvl))}  ELO {code(str(elo_val))}"
        )

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=with_navigation(),
    )
