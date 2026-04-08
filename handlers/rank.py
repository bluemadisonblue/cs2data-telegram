"""ELO progress + nav shortcut."""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import database as dbmod
from config import COOLDOWN_SEC, LEVEL_ELO_RANGES, elo_progress_in_level, level_tier_emoji
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    extract_cs2_game,
)
from faceit_messages import html_faceit_transport_error
from keyboards.inline import ctx_rank_kb, player_links_kb, with_navigation
from ui_text import bold, code, esc, italic, not_linked_html, section, sep

router = Router(name="rank")

logger = logging.getLogger(__name__)

_last: dict[int, float] = {}


async def _cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last[user_id] = now
    return None


def _progress_bar(frac: float, width: int = 14) -> str:
    filled = int(round(frac * width))
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    pct = int(round(frac * 100))
    return f"{code('[' + bar + ']')}  {bold(str(pct) + '%')}"


async def answer_rank_card(
    message: Message,
    db,
    faceit,
    *,
    actor_telegram_id: int | None = None,
) -> None:
    uid = actor_telegram_id if actor_telegram_id is not None else message.from_user.id
    u = await dbmod.get_user(db, uid)
    if not u:
        await message.answer(
            not_linked_html(),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    pid = u["faceit_player_id"]

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        p = await faceit.get_player_by_id(pid)
    except FaceitNotFoundError:
        await message.answer(bold("Player not found."), parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
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
    frac, _band_lo, next_min = elo_progress_in_level(elo, level)
    nick = esc(p.get("nickname") or u["faceit_nickname"])

    lines: list[str] = [
        section("🏆", "Skill & ELO"),
        f"{tier} <b>{nick}</b>",
        sep(24),
        f"{bold('ELO')} {code(str(elo))}",
        f"{bold('Level')} {code(str(level))}",
        sep(24),
        section("📈", "Progress in current level"),
        _progress_bar(frac),
    ]

    if level >= 10:
        lines.append(italic("Max visible band — keep grinding for Challenger leaderboard."))
    elif next_min is not None:
        need = max(0, next_min - elo)
        lines.append(italic(f"~{need} ELO to level {level + 1} floor ({next_min})."))
    else:
        lines.append(italic(f"~{frac * 100:.0f}% through this level band."))

    lines.append("")
    lines.append(section("📚", "CS2 ELO bands"))
    for lv, lo, hi in LEVEL_ELO_RANGES:
        em = level_tier_emoji(lv)
        if lv == 10:
            lines.append(f"{em} L{lv}  {code(str(lo) + '+')}")
        else:
            lines.append(f"{em} L{lv}  {code(str(lo))} → {code(str(hi))}")

    url_kb = player_links_kb(str(p.get("faceit_url") or ""))
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=ctx_rank_kb(url_kb))

    # Record ELO snapshot
    if elo:
        try:
            await dbmod.record_elo_snapshot(db, uid, elo, level)
        except Exception as exc:
            logger.warning("record_elo_snapshot failed: %s", exc, exc_info=True)


@router.message(Command("rank"))
@router.message(Command("elo"))
async def cmd_rank(message: Message, db, faceit) -> None:
    if msg := await _cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    await answer_rank_card(message, db, faceit)


@router.callback_query(F.data == "nav:rank")
async def cb_nav_rank(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := await _cooldown(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await answer_rank_card(callback.message, db, faceit, actor_telegram_id=callback.from_user.id)
