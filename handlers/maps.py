"""Map frequency from recent FACEIT match rows (CS2)."""

from __future__ import annotations

import time
from collections import Counter

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import database as dbmod
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    parse_match_stats_row,
)
from keyboards.inline import with_navigation
from ui_text import bold, code, esc, italic, section, sep

router = Router(name="maps")

COOLDOWN_SEC = 10.0
_last: dict[int, float] = {}


async def _cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last[user_id] = now
    return None


async def _need_user(
    message: Message,
    db,
    *,
    actor_telegram_id: int | None = None,
):
    uid = actor_telegram_id if actor_telegram_id is not None else message.from_user.id
    u = await dbmod.get_user(db, uid)
    if not u:
        await message.answer(
            f"{bold('Account not linked')}\n"
            f"Use {code('/register your_faceit_nickname')} first.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
    return u


async def answer_maps_mix(
    message: Message,
    db,
    faceit,
    *,
    limit: int = 40,
    actor_telegram_id: int | None = None,
) -> None:
    user = await _need_user(message, db, actor_telegram_id=actor_telegram_id)
    if not user:
        return

    pid = user["faceit_player_id"]
    limit = max(10, min(60, limit))

    try:
        raw = await faceit.get_player_match_stats(pid, limit=limit, offset=0)
    except FaceitNotFoundError:
        await message.answer(
            bold("Player not found."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitUnavailableError:
        await message.answer(
            bold("FACEIT is temporarily unavailable."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitRateLimitError:
        await message.answer(
            bold("FACEIT rate limit."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitAPIError:
        await message.answer(
            bold("FACEIT error."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    items = (raw or {}).get("items") or []
    ctr: Counter[str] = Counter()
    unknown = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        stats = it.get("stats")
        if not isinstance(stats, dict):
            unknown += 1
            continue
        row = parse_match_stats_row(stats)
        m = row.get("map") or "—"
        if m and m != "—":
            ctr[str(m)] += 1
        else:
            unknown += 1

    nick = esc(user["faceit_nickname"])
    lines: list[str] = [
        section("🗺", "Recent map mix"),
        f"<b>{nick}</b>  ·  {italic(f'last {len(items)} matches from API')}",
        sep(26),
    ]

    if not ctr:
        lines.append(italic("No map names in this batch (FACEIT may omit them for some rows)."))
    else:
        lines.append(f"{bold('Distinct maps')} {code(str(len(ctr)))}")
        lines.append("")
        for name, cnt in ctr.most_common(12):
            lines.append(f"{code(str(cnt) + '×')}  {esc(name)}")
        if len(ctr) > 12:
            lines.append(italic(f"+ {len(ctr) - 12} more map(s) in the data"))
    if unknown:
        lines.append("")
        lines.append(italic(f"{unknown} row(s) without a map label"))

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=with_navigation(),
    )


@router.message(Command("maps"))
async def cmd_maps(message: Message, command: CommandObject, db, faceit) -> None:
    if msg := await _cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    lim = 40
    if command.args:
        try:
            lim = int(command.args.split()[0])
        except (ValueError, IndexError):
            lim = 40
    await answer_maps_mix(message, db, faceit, limit=lim)


@router.callback_query(F.data == "nav:maps")
async def cb_nav_maps(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := await _cooldown(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    await answer_maps_mix(
        callback.message,
        db,
        faceit,
        limit=40,
        actor_telegram_id=callback.from_user.id,
    )
