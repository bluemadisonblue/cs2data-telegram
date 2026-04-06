"""Compare two players — monospace table in &lt;pre&gt; for alignment."""

from __future__ import annotations

import html
import time

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import database as dbmod
from config import level_tier_emoji
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    extract_cs2_game,
    parse_lifetime_stats,
)
from formatting import flag_emoji
from keyboards.inline import with_navigation
from ui_text import bold, code, italic, section

router = Router(name="compare")

COOLDOWN_SEC = 10.0
_last: dict[int, float] = {}


async def _cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before comparing again."
    _last[user_id] = now
    return None


async def _fetch_bundle(faceit, player_id: str):
    p = await faceit.get_player_by_id(player_id)
    st = await faceit.get_player_stats_lifetime(player_id)
    g = extract_cs2_game(p) or {}
    elo = int(g.get("faceit_elo") or 0)
    level = int(g.get("skill_level") or 0)
    life = (st.get("lifetime") or {}) if isinstance(st, dict) else {}
    if not isinstance(life, dict):
        life = {}
    parsed = parse_lifetime_stats(life)
    wr = parsed["win_rate_pct"]
    wr_s = f"{wr:.1f}%" if wr is not None else "N/A"
    kd = parsed["kd"]
    kd_s = f"{kd:.2f}" if kd is not None else "N/A"
    hs = parsed["hs_pct"]
    hs_s = f"{hs:.1f}%" if hs is not None else "N/A"
    mp = parsed.get("matches")
    mp_s = str(int(mp)) if mp is not None else "N/A"
    wn = parsed.get("wins")
    ls = parsed.get("losses")
    wl_s = "N/A"
    if wn is not None and ls is not None:
        wl_s = f"{int(wn)}/{int(ls)}"
    mvp_s = "N/A"
    if parsed.get("mvps") is not None:
        try:
            mvp_s = str(int(float(parsed["mvps"])))
        except (TypeError, ValueError):
            mvp_s = "N/A"
    kr = parsed.get("kr")
    kr_s = f"{kr:.2f}" if kr is not None else "N/A"
    cc = (p.get("country") or "").upper()
    return {
        "nickname": p.get("nickname") or "?",
        "elo": elo,
        "level": level,
        "kd": kd_s,
        "wr": wr_s,
        "hs": hs_s,
        "matches": mp_s,
        "wl": wl_s,
        "mvps": mvp_s,
        "kr": kr_s,
        "country": cc,
        "flag": flag_emoji(cc),
    }


def _compare_table(you: dict, opp: dict) -> str:
    y_tier = level_tier_emoji(you["level"]) if you["level"] else ""
    o_tier = level_tier_emoji(opp["level"]) if opp["level"] else ""
    rows = [
        f"{'Stat':<11} {'You':<16} {'Opponent':<16}",
        f"{'─'*11} {'─'*16} {'─'*16}",
        f"{'ELO':<11} {str(you['elo']):<16} {str(opp['elo']):<16}",
        f"{'Level':<11} {(y_tier + str(you['level'])):<16} {(o_tier + str(opp['level'])):<16}",
        f"{'Matches':<11} {you['matches']:<16} {opp['matches']:<16}",
        f"{'W/L':<11} {you['wl']:<16} {opp['wl']:<16}",
        f"{'Win%':<11} {you['wr']:<16} {opp['wr']:<16}",
        f"{'K/D':<11} {you['kd']:<16} {opp['kd']:<16}",
        f"{'K/R':<11} {you['kr']:<16} {opp['kr']:<16}",
        f"{'HS%':<11} {you['hs']:<16} {opp['hs']:<16}",
        f"{'MVPs':<11} {you['mvps']:<16} {opp['mvps']:<16}",
    ]
    return "<pre>" + html.escape("\n".join(rows)) + "</pre>"


@router.message(Command("compare"))
async def cmd_compare(message: Message, command: CommandObject, db, faceit) -> None:
    if msg := await _cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return

    if not command.args or not command.args.strip():
        await message.answer(
            f"{bold('Usage')}: {code('/compare faceit_nickname')}",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    me = await dbmod.get_user(db, message.from_user.id)
    if not me:
        await message.answer(
            f"{bold('Account not linked')}\n"
            f"Use {code('/register your_faceit_nickname')} first.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    opp_nick = command.args.strip()

    try:
        you = await _fetch_bundle(faceit, me["faceit_player_id"])
        opp_pl = await faceit.get_player_by_nickname(opp_nick)
        opp_id = opp_pl.get("player_id")
        if not opp_id:
            raise FaceitAPIError("no id")
        opp = await _fetch_bundle(faceit, opp_id)
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

    yf = you.get("flag") or ""
    of = opp.get("flag") or ""
    header = (
        f"{section('⚔️', 'Head-to-head')}\n"
        f"<b>{html.escape(you['nickname'])}</b> {yf} vs "
        f"<b>{html.escape(opp['nickname'])}</b> {of}\n"
    )
    body = _compare_table(you, opp)

    await message.answer(
        header + body,
        parse_mode=ParseMode.HTML,
        reply_markup=with_navigation(),
    )


@router.callback_query(F.data == "nav:compare")
async def cb_nav_compare(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            f"{bold('Compare')}\n"
            f"Send {code('/compare faceit_nickname')} to compare your stats with another player.\n"
            f"{italic('You must be registered with /register first.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
    await callback.answer()
