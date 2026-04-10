"""FACEIT profile card + nav shortcut."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, URLInputFile

import database as dbmod
from config import level_tier_emoji
from handlers.cooldown import check_cooldown
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    extract_cs2_game,
    steam_community_url,
)
from faceit_messages import html_faceit_transport_error
from formatting import flag_emoji
from keyboards.inline import ctx_profile_kb, player_links_kb, with_navigation
from ui_text import bold, code, esc, link, not_linked_html, section, sep

router = Router(name="profile")

logger = logging.getLogger(__name__)

_CAPTION_MAX = 1024


async def answer_profile_card(
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
        await message.answer(
            bold("Player not found."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
        await message.answer(
            html_faceit_transport_error(exc),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    g = extract_cs2_game(p) or {}
    nick = p.get("nickname") or u["faceit_nickname"]
    elo = int(g.get("faceit_elo") or 0)
    level = int(g.get("skill_level") or 0)
    tier = level_tier_emoji(level) if level else "❔"
    region = str(g.get("region") or "—")
    country = (p.get("country") or "").upper()
    flg = flag_emoji(country)
    steam = p.get("steam_nickname") or ""
    steam_url = steam_community_url(p)
    faceit_url = str(p.get("faceit_url") or "")
    url_kb = player_links_kb(faceit_url)

    lines = [
        section("👤", "FACEIT profile"),
        f"{tier} <b>{esc(nick)}</b> {flg}".rstrip(),
        sep(24),
        f"{bold('ELO')} {code(str(elo))}   {bold('Level')} {code(str(level))}",
        f"{bold('Region')} {code(region)}",
    ]
    if country:
        lines.append(f"{bold('Country')} {code(country)}")
    if steam:
        lines.append("")
        lines.append(section("🎮", "Steam"))
        if steam_url:
            lines.append(link(steam_url, steam))
        else:
            lines.append(code(steam))

    detail = "\n".join(lines)
    avatar = p.get("avatar")
    markup = ctx_profile_kb(url_kb)

    if avatar and str(avatar).startswith("https://") and len(detail) <= _CAPTION_MAX:
        try:
            await message.answer_photo(
                photo=URLInputFile(str(avatar)),
                caption=detail,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return
        except Exception as exc:
            logger.warning(
                "Profile photo send failed (%s); falling back to text.",
                exc,
                exc_info=True,
            )

    # No avatar, photo failed, or caption too long for one media message
    await message.answer(detail, parse_mode=ParseMode.HTML, reply_markup=markup)


@router.message(Command("profile"))
async def cmd_profile(message: Message, db, faceit) -> None:
    if msg := check_cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    await answer_profile_card(message, db, faceit)


@router.callback_query(F.data == "nav:profile")
async def cb_nav_profile(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := check_cooldown(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await answer_profile_card(
        callback.message,
        db,
        faceit,
        actor_telegram_id=callback.from_user.id,
    )
