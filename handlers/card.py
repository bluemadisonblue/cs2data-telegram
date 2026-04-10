"""Share card: /card [nickname] — generates a shareable PNG stats image."""

from __future__ import annotations

import logging
from io import BytesIO

from aiogram import Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

import database as dbmod
from card_generator import generate_stats_card
from faceit_api import FaceitAPIError, FaceitNotFoundError, FaceitRateLimitError, FaceitUnavailableError
from faceit_messages import html_faceit_transport_error
from handlers.cooldown import check_cooldown
from keyboards.inline import card_share_kb, with_navigation
from stats_format import fetch_stats_bundle
from ui_text import bold, code, esc, italic, not_linked_html

router = Router(name="card")

logger = logging.getLogger(__name__)


@router.message(Command("card"))
async def cmd_card(message: Message, command: CommandObject, db, faceit) -> None:
    if msg := check_cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return

    nickname_arg = (command.args or "").strip() or None

    # Resolve whose stats to show
    if nickname_arg:
        # Any player by nickname — no registration required
        lookup_kw = {"nickname": nickname_arg}
    else:
        # Own linked account
        u = await dbmod.get_user(db, message.from_user.id)
        if not u:
            await message.answer(
                not_linked_html()
                + "\n\n"
                + italic("Or use /card nickname to generate a card for any player."),
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return
        lookup_kw = {"player_id": u["faceit_player_id"]}

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    loading = await message.answer("🎨 Generating stats card…")

    try:
        bundle = await fetch_stats_bundle(faceit, **lookup_kw)
    except FaceitNotFoundError:
        await loading.delete()
        hint = (
            italic(f'No FACEIT profile found for "{nickname_arg}". Check the spelling.')
            if nickname_arg
            else ""
        )
        await message.answer(
            bold("Player not found.") + ("\n" + hint if hint else ""),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
        await loading.delete()
        await message.answer(
            html_faceit_transport_error(exc),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    try:
        png_bytes = generate_stats_card(bundle)
    except Exception as exc:
        await loading.delete()
        logger.error("generate_stats_card failed: %s", exc, exc_info=True)
        await message.answer(
            bold("Could not generate card.") + "\nTry again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    await loading.delete()

    nick = bundle.get("nickname") or "player"
    caption = f"📊 <b>{esc(nick)}</b> · CS2 FACEIT stats"

    safe_filename = "".join(c for c in nick if c.isalnum() or c in "-_") or "player"
    photo = BufferedInputFile(png_bytes, filename=f"{safe_filename}_cs2stats.png")
    await message.answer_photo(
        photo=photo,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=card_share_kb(nick),
    )
