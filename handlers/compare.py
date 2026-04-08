"""Compare two players — monospace table in <pre> with winner highlighted."""

from __future__ import annotations

import asyncio
import html
import time

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import database as dbmod
from config import COOLDOWN_SEC, level_tier_emoji
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    extract_cs2_game,
    lifetime_map_from_stats_response,
    parse_lifetime_stats,
)
from faceit_messages import html_faceit_transport_error
from formatting import flag_emoji
from keyboards.inline import ctx_compare_kb, with_navigation
from ui_text import bold, code, italic, not_linked_html, section

router = Router(name="compare")

_last: dict[int, float] = {}


async def _cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last[user_id] = now
    return None


async def _fetch_bundle(faceit, player_id: str) -> dict:
    """Fetch player profile + lifetime stats concurrently."""
    p, st = await asyncio.gather(
        faceit.get_player_by_id(player_id),
        faceit.get_player_stats_lifetime(player_id),
    )
    g = extract_cs2_game(p) or {}
    elo = int(g.get("faceit_elo") or 0)
    level = int(g.get("skill_level") or 0)
    life = lifetime_map_from_stats_response(st if isinstance(st, dict) else None)
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
    wrp = parsed.get("win_rate_pct")
    # Belt-and-suspenders if enrich missed (FACEIT label quirks per account).
    if (wn is None or ls is None) and mp is not None and wrp is not None:
        try:
            mf = float(mp)
            wrf = float(wrp)
            if mf > 0:
                mi = int(round(mf))
                w_est = int(round(mf * wrf / 100.0))
                w_est = max(0, min(w_est, mi))
                if wn is None:
                    wn = float(w_est)
                if ls is None:
                    ls = float(mi - w_est)
        except (TypeError, ValueError):
            pass
    if wn is not None and ls is None and mp is not None:
        try:
            ls = max(0.0, float(mp) - float(wn))
        except (TypeError, ValueError):
            pass
    elif ls is not None and wn is None and mp is not None:
        try:
            wn = max(0.0, float(mp) - float(ls))
        except (TypeError, ValueError):
            pass
    wl_s = "N/A"
    if wn is not None and ls is not None:
        wl_s = f"{int(wn)}/{int(ls)}"
    mvp_s = "N/A"
    mv = parsed.get("mvps")
    if mv is not None:
        try:
            mvp_s = str(int(float(mv)))
        except (TypeError, ValueError):
            mvp_s = "N/A"
    kr = parsed.get("kr")
    if kr is None and parsed.get("kills") is not None and parsed.get("rounds"):
        try:
            rf = float(parsed["rounds"])
            if rf > 0:
                kr = float(parsed["kills"]) / rf
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    kr_s = f"{kr:.2f}" if kr is not None else "N/A"
    cc = (p.get("country") or "").upper()
    return {
        "nickname": p.get("nickname") or "?",
        "elo": elo,
        "level": level,
        # raw floats for comparison
        "kd_raw": kd,
        "wr_raw": wr,
        "hs_raw": hs,
        "mp_raw": mp,
        "kr_raw": kr,
        "mvps_raw": mv,
        # formatted strings for display
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


async def fetch_bundle_for_nickname(faceit, nickname: str) -> dict:
    """Resolve FACEIT nickname and return the same dict as _fetch_bundle."""
    pl = await faceit.get_player_by_nickname(nickname.strip())
    pid = pl.get("player_id")
    if not pid:
        raise FaceitAPIError("no id")
    return await _fetch_bundle(faceit, pid)


def _win_marker(a: float | None, b: float | None, higher_is_better: bool = True) -> tuple[str, str]:
    """Return (' <', '') or ('', ' <') to mark the winner, or ('', '') if tied/unknown."""
    if a is None or b is None:
        return "", ""
    if a == b:
        return "", ""
    you_wins = (a > b) == higher_is_better
    return (" <", "") if you_wins else ("", " <")


def _compare_table(you: dict, opp: dict) -> str:
    y_tier = level_tier_emoji(you["level"]) if you["level"] else ""
    o_tier = level_tier_emoji(opp["level"]) if opp["level"] else ""

    # ELO comparison (higher = better)
    elo_ym, elo_om = _win_marker(float(you["elo"]), float(opp["elo"]))
    # Level comparison (higher = better)
    lvl_ym, lvl_om = _win_marker(float(you["level"]), float(opp["level"]))
    # Matches comparison (more = more experienced, neutral — no marker)
    # Win rate (higher = better)
    wr_ym, wr_om = _win_marker(you["wr_raw"], opp["wr_raw"])
    # K/D (higher = better)
    kd_ym, kd_om = _win_marker(you["kd_raw"], opp["kd_raw"])
    # K/R (higher = better)
    kr_ym, kr_om = _win_marker(you["kr_raw"], opp["kr_raw"])
    # HS% (higher = better)
    hs_ym, hs_om = _win_marker(you["hs_raw"], opp["hs_raw"])
    # MVPs (higher = better)
    mvp_ym, mvp_om = _win_marker(you["mvps_raw"], opp["mvps_raw"])

    y_elo = str(you["elo"]) + elo_ym
    o_elo = str(opp["elo"]) + elo_om
    y_lvl = y_tier + str(you["level"]) + lvl_ym
    o_lvl = o_tier + str(opp["level"]) + lvl_om

    rows = [
        f"{'Stat':<11} {'You':<18} {'Opponent':<18}",
        f"{'─'*11} {'─'*18} {'─'*18}",
        f"{'ELO':<11} {y_elo:<18} {o_elo:<18}",
        f"{'Level':<11} {y_lvl:<18} {o_lvl:<18}",
        f"{'Matches':<11} {you['matches']:<18} {opp['matches']:<18}",
        f"{'W/L':<11} {you['wl']:<18} {opp['wl']:<18}",
        f"{'Win%':<11} {(you['wr'] + wr_ym):<18} {(opp['wr'] + wr_om):<18}",
        f"{'K/D':<11} {(you['kd'] + kd_ym):<18} {(opp['kd'] + kd_om):<18}",
        f"{'K/R':<11} {(you['kr'] + kr_ym):<18} {(opp['kr'] + kr_om):<18}",
        f"{'HS%':<11} {(you['hs'] + hs_ym):<18} {(opp['hs'] + hs_om):<18}",
        f"{'MVPs':<11} {(you['mvps'] + mvp_ym):<18} {(opp['mvps'] + mvp_om):<18}",
        "",
        "< = winner in this stat",
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
            not_linked_html(),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    opp_nick = command.args.strip()

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer(f"⏳ Comparing with {html.escape(opp_nick)}…", parse_mode=ParseMode.HTML)

    try:
        # Run my bundle fetch and opponent nickname lookup concurrently
        my_task = asyncio.create_task(_fetch_bundle(faceit, me["faceit_player_id"]))
        opp_pl = await faceit.get_player_by_nickname(opp_nick)
        opp_id = opp_pl.get("player_id")
        if not opp_id:
            raise FaceitAPIError("no id")
        you, opp = await asyncio.gather(my_task, _fetch_bundle(faceit, opp_id))
    except FaceitNotFoundError:
        await loading.delete()
        await message.answer(
            f"{bold('Player not found.')}\n"
            f"{italic(f'Could not find \"{html.escape(opp_nick)}\" on FACEIT. Double-check the spelling.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
        return
    except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
        await loading.delete()
        await message.answer(
            html_faceit_transport_error(exc),
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
        return

    await loading.delete()

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
        reply_markup=ctx_compare_kb(),
    )


@router.callback_query(F.data == "nav:compare")
async def cb_nav_compare(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(
            f"{bold('Compare / party')}\n"
            f"{code('/compare nick')} — you vs one player (registered).\n"
            f"{code('/party nick1 nick2 …')} — multi-player table (no registration).\n"
            f"{italic('/compare requires /register; /party does not.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
    await callback.answer()
