"""Commands: /stats [nickname], /matches, /match + nav button shortcuts."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import database as dbmod
from config import COOLDOWN_SEC, MATCHES_PAGE_SIZE
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    aggregate_match_scoreboard,
    group_rows_by_team,
    parse_match_stats_row,
    resolve_match_faceit_url,
)
from formatting import flag_emoji, pick_history_meta
from stats_format import fetch_stats_bundle, format_stats_dashboard_html
from keyboards.inline import (
    ctx_matches_kb,
    ctx_scoreboard_kb,
    ctx_stats_kb,
    match_boards_kb,
    match_faceit_kb,
    matches_pagination_kb,
    player_links_kb,
    with_match_boards_and_nav,
    with_navigation,
)
from ui_text import bold, code, esc, italic, section

router = Router(name="stats")

logger = logging.getLogger(__name__)

_last_heavy: dict[int, float] = {}


async def _cooldown_block(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last_heavy.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last_heavy[user_id] = now
    return None


def _roster_pre_block(rows: list[dict], my_pid: str) -> str:
    """Fixed-width roster for <pre> (aligned K/D/A/HS)."""
    lines: list[str] = []
    for r in rows:
        hs = r["hs_pct"]
        hs_s = f"{hs:.0f}%" if hs is not None else "—"
        mark = "→ " if r["player_id"] == my_pid else "   "
        nick = str(r["nickname"])
        if len(nick) > 14:
            nick = nick[:13] + "…"
        nick = nick.ljust(14)
        line = (
            f"{mark}{nick} {int(r['kills']):>2} {int(r['deaths']):>2} "
            f"{int(r['assists']):>2}  {hs_s:>4}"
        )
        lines.append(line)
    return "\n".join(lines)


def _match_scoreboard_header_lines(meta: dict) -> list[str]:
    """Title + one line: event · region/status · score (no raw match id)."""
    lines: list[str] = [section("🧾", "Match scoreboard")]
    if not meta:
        return lines
    bits: list[str] = []
    comp = meta.get("competition_name")
    if comp:
        bits.append(italic(str(comp)[:80]))
    reg = meta.get("region")
    st = meta.get("status")
    meta_bits: list[str] = []
    if reg:
        meta_bits.append(code(str(reg)))
    if st:
        meta_bits.append(code(str(st)))
    if meta_bits:
        bits.append(" · ".join(meta_bits))
    res = meta.get("results")
    if isinstance(res, dict) and res.get("score"):
        sc = res["score"]
        if isinstance(sc, dict) and sc:
            try:
                vs = sorted((int(x) for x in sc.values()), reverse=True)
                if len(vs) >= 2:
                    bits.append(f"{bold('Score')} {code(f'{vs[0]} – {vs[1]}')}")
            except (TypeError, ValueError):
                pass
    if bits:
        lines.append(" · ".join(bits))
    return lines


async def _need_user(message: Message, db, *, actor_telegram_id: int | None = None):
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


async def send_match_scoreboard(
    message: Message,
    db,
    faceit,
    match_id: str,
    actor_telegram_id: int,
) -> None:
    user = await dbmod.get_user(db, actor_telegram_id)
    if not user:
        await message.answer(
            f"{bold('Account not linked')}\n"
            f"Use {code('/register your_faceit_nickname')} first.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    my_pid = user["faceit_player_id"]
    mid = match_id.strip()

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer("⏳ Loading scoreboard…")

    meta: dict = {}
    try:
        meta = await faceit.get_match(mid)
    except (FaceitNotFoundError, FaceitAPIError, FaceitUnavailableError, FaceitRateLimitError):
        meta = {}

    try:
        ms = await faceit.get_match_stats(mid)
    except FaceitNotFoundError:
        await loading.delete()
        await message.answer(
            f"{bold('Match not found.')}\n"
            f"{italic('Check the match ID or use /matches to browse your history.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_scoreboard_kb(),
        )
        return
    except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
        await loading.delete()
        msg = bold("FACEIT rate limit.") if isinstance(exc, FaceitRateLimitError) else bold("FACEIT is temporarily unavailable.")
        await message.answer(msg + " Try again shortly.", parse_mode=ParseMode.HTML, reply_markup=ctx_scoreboard_kb())
        return

    await loading.delete()

    rows = aggregate_match_scoreboard(ms)
    if not rows:
        await message.answer(bold("No scoreboard data for this match."), parse_mode=ParseMode.HTML, reply_markup=ctx_scoreboard_kb())
        return

    team_a, team_b = group_rows_by_team(rows)

    body: list[str] = _match_scoreboard_header_lines(meta) + [""]
    if team_a:
        body.append(bold("Team A"))
        body.append(f"<pre>{html.escape(_roster_pre_block(team_a, my_pid))}</pre>")
    if team_b:
        body.append("")
        body.append(bold("Team B"))
        body.append(f"<pre>{html.escape(_roster_pre_block(team_b, my_pid))}</pre>")
    if not team_b and team_a:
        body.append(italic("Only one team in payload."))

    faceit_url = resolve_match_faceit_url(meta, mid)
    await message.answer(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=ctx_scoreboard_kb(match_faceit_kb(faceit_url)),
    )


async def answer_stats_dashboard(
    message: Message,
    db,
    faceit,
    *,
    actor_telegram_id: int | None = None,
    lookup_nickname: str | None = None,
) -> None:
    """Show a full CS2 stats dashboard.

    If *lookup_nickname* is given, look up that player (no registration needed).
    Otherwise show the calling user's own linked account.
    """
    uid = actor_telegram_id if actor_telegram_id is not None else message.from_user.id

    if lookup_nickname:
        own_account = None
        loading_text = f"⏳ Fetching stats for {html.escape(lookup_nickname)}…"
    else:
        own_account = await _need_user(message, db, actor_telegram_id=uid)
        if not own_account:
            return
        loading_text = "⏳ Fetching your stats…"

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer(loading_text, parse_mode=ParseMode.HTML)

    try:
        if lookup_nickname:
            bundle = await fetch_stats_bundle(faceit, nickname=lookup_nickname)
        else:
            bundle = await fetch_stats_bundle(
                faceit, player_id=own_account["faceit_player_id"]
            )
    except FaceitNotFoundError:
        await loading.delete()
        hint = (
            f"\n{italic(f'No FACEIT profile found for \"{html.escape(lookup_nickname)}\". Check the spelling.')}"
            if lookup_nickname
            else ""
        )
        await message.answer(
            bold("Player not found.") + hint,
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitUnavailableError:
        await loading.delete()
        await message.answer(
            bold("FACEIT is temporarily unavailable.") + "\nTry again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitRateLimitError:
        await loading.delete()
        await message.answer(
            bold("FACEIT rate limit.") + " Try again shortly.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitAPIError:
        await loading.delete()
        await message.answer(
            bold("FACEIT error.") + " Try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    await loading.delete()

    text = format_stats_dashboard_html(
        bundle,
        bot_username=message.bot.username if message.bot else None,
    )
    url_kb = player_links_kb(bundle["faceit_url"])
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=ctx_stats_kb(url_kb),
    )

    if own_account and bundle["elo"]:
        try:
            await dbmod.record_elo_snapshot(db, uid, bundle["elo"], bundle["level"])
        except Exception as exc:
            logger.warning("record_elo_snapshot failed: %s", exc, exc_info=True)


async def answer_matches_list(
    message: Message,
    db,
    faceit,
    limit: int = 10,
    page: int = 1,
    *,
    actor_telegram_id: int | None = None,
) -> None:
    user = await _need_user(message, db, actor_telegram_id=actor_telegram_id)
    if not user:
        return

    pid = user["faceit_player_id"]
    limit = max(1, min(20, limit))
    page = max(1, page)

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer("⏳ Fetching matches…")

    try:
        raw, hist = await asyncio.gather(
            faceit.get_player_match_stats(pid, limit=limit, offset=0),
            faceit.get_player_history(pid, limit=limit),
        )
    except FaceitNotFoundError:
        await loading.delete()
        await message.answer(bold("Player not found."), parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
        await loading.delete()
        msg = bold("FACEIT rate limit.") if isinstance(exc, FaceitRateLimitError) else bold("FACEIT is temporarily unavailable.")
        await message.answer(msg + " Try again shortly.", parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return

    await loading.delete()

    hist_map: dict[str, dict] = {}
    for h in hist.get("items") or []:
        if not isinstance(h, dict):
            continue
        hid = h.get("match_id") or h.get("ID")
        if hid:
            hist_map[str(hid)] = h

    items = raw.get("items") or []
    all_board_entries: list[tuple[str, str]] = []
    nick_label = esc(user["faceit_nickname"])

    for idx, it in enumerate(items, start=1):
        stats = it.get("stats") if isinstance(it, dict) else None
        if not isinstance(stats, dict):
            continue
        row = parse_match_stats_row(stats)
        mid = row.get("match_id") or (str(it.get("match_id")) if isinstance(it, dict) and it.get("match_id") else None)
        if mid:
            row["match_id"] = mid

        wl = "W" if row["won"] is True else ("L" if row["won"] is False else "?")
        kd_val = row["kd"]
        kd_s = f"{kd_val:.2f}" if kd_val is not None else "—"
        map_raw = (row["map"] or "—").strip() or "—"

        score_s = "—"
        if mid and mid in hist_map:
            meta = pick_history_meta(hist_map[mid])
            if meta.get("score"):
                score_s = str(meta["score"]).replace("–", "-")
        if len(score_s) > 7:
            score_s = score_s[:6] + "…"

        if not mid:
            continue

        map_btn = map_raw if map_raw != "—" else "?"
        tail = score_s if score_s != "—" else f"K/D {kd_s}"
        label = f"{idx:02d} {wl} {map_btn} · {tail}"
        while len(label) > 64 and len(map_btn) > 5:
            map_btn = map_btn[:-2] + "…" if len(map_btn) > 2 else "?"
            label = f"{idx:02d} {wl} {map_btn} · {tail}"
        if len(label) > 64:
            label = label[:64]
        all_board_entries.append((str(mid), label))

    if not all_board_entries:
        await message.answer(
            "\n".join([section("📜", "Recent matches"), f"{bold('Player')}: {nick_label}", "", italic("No recent matches returned by FACEIT.")]),
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_matches_kb(),
        )
        return

    total_rows = len(all_board_entries)
    total_pages = max(1, (total_rows + MATCHES_PAGE_SIZE - 1) // MATCHES_PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * MATCHES_PAGE_SIZE
    end = start + MATCHES_PAGE_SIZE

    page_entries = all_board_entries[start:end]

    caption_lines = [
        section("📜", "Recent matches"),
        f"🎮 <b>{nick_label}</b> · {total_rows} game{'s' if total_rows != 1 else ''}",
        italic("Row · scoreboard  ·  🌐 FACEIT"),
    ]
    boards = match_boards_kb(page_entries)
    pagination = matches_pagination_kb(page, total_pages, limit)
    nav = with_match_boards_and_nav(boards, pagination)
    await message.answer("\n".join(caption_lines), parse_mode=ParseMode.HTML, reply_markup=nav)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject, db, faceit) -> None:
    if msg := await _cooldown_block(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    nickname = (command.args or "").strip() or None
    await answer_stats_dashboard(message, db, faceit, lookup_nickname=nickname)


@router.message(Command("matches"))
async def cmd_matches(message: Message, command: CommandObject, db, faceit) -> None:
    if msg := await _cooldown_block(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    limit = 10
    if command.args:
        try:
            limit = int(command.args.split()[0])
        except (ValueError, IndexError):
            limit = 10
    await answer_matches_list(message, db, faceit, limit=limit, page=1)


_MATCH_ID_RE = re.compile(r"^[a-fA-F0-9\-]{8,}$")


@router.message(Command("match"))
async def cmd_match(message: Message, command: CommandObject, db, faceit) -> None:
    mid = (command.args or "").strip()
    if not mid or not _MATCH_ID_RE.match(mid):
        await message.answer(
            f"{bold('Usage')}: {code('/match match_id')}\n"
            f"{italic('Use the numbered buttons after /matches, or paste an id from FACEIT.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    if msg := await _cooldown_block(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    await send_match_scoreboard(message, db, faceit, mid, message.from_user.id)


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("m:"))
async def cb_match_board(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    mid = callback.data[2:].strip()
    if not mid or not _MATCH_ID_RE.match(mid):
        await callback.answer("Invalid match id", show_alert=True)
        return
    if msg := await _cooldown_block(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_match_scoreboard(callback.message, db, faceit, mid, callback.from_user.id)


@router.callback_query(F.data.startswith("matches:p:"))
async def cb_matches_page(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    try:
        page = int(parts[2])
        limit = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Invalid page", show_alert=True)
        return
    if msg := await _cooldown_block(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await answer_matches_list(
        callback.message, db, faceit, limit=limit, page=page,
        actor_telegram_id=callback.from_user.id,
    )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "nav:stats")
async def cb_nav_stats(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := await _cooldown_block(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await answer_stats_dashboard(callback.message, db, faceit, actor_telegram_id=callback.from_user.id)


@router.callback_query(F.data == "nav:matches")
async def cb_nav_matches(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := await _cooldown_block(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await answer_matches_list(
        callback.message, db, faceit, limit=10, page=1,
        actor_telegram_id=callback.from_user.id,
    )
