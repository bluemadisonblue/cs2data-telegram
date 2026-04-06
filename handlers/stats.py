"""Commands: /stats, /matches, /match + nav button shortcuts."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

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
    aggregate_match_scoreboard,
    extract_cs2_game,
    group_rows_by_team,
    parse_lifetime_stats,
    parse_match_stats_row,
)
from formatting import flag_emoji, pick_history_meta, recent_form_badge
from keyboards.inline import match_faceit_kb, player_links_kb, with_navigation
from ui_text import bold, code, esc, italic, section, sep

router = Router(name="stats")

COOLDOWN_SEC = 10.0
_last_heavy: dict[int, float] = {}


async def _cooldown_block(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last_heavy.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last_heavy[user_id] = now
    return None


def _fmt_ts(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            return val[:48]
    try:
        ts = float(val)
        if ts > 1e12:
            ts /= 1000.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return str(val)[:48]


def _fmt_opt(v: float | None, fmt: str, fallback: str = "—") -> str:
    if v is None:
        return fallback
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return fallback


async def _need_user(
    message: Message,
    db,
    *,
    actor_telegram_id: int | None = None,
):
    """
    actor_telegram_id: use for callback flows. Bot-sent messages have
    message.from_user = the bot; the clicker is callback.from_user.
    """
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


def _split_message_lines(lines: list[str], max_len: int = 3900) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    n = 0
    for line in lines:
        add = len(line) + 1
        if n + add > max_len and buf:
            chunks.append("\n".join(buf))
            buf = [line]
            n = add
        else:
            buf.append(line)
            n += add
    if buf:
        chunks.append("\n".join(buf))
    return chunks


async def answer_stats_dashboard(
    message: Message,
    db,
    faceit,
    *,
    actor_telegram_id: int | None = None,
) -> None:
    user = await _need_user(message, db, actor_telegram_id=actor_telegram_id)
    if not user:
        return

    pid = user["faceit_player_id"]
    nick = user["faceit_nickname"]

    try:
        p, st, recent_raw = await asyncio.gather(
            faceit.get_player_by_id(pid),
            faceit.get_player_stats_lifetime(pid),
            faceit.get_player_match_stats(pid, limit=12, offset=0),
        )
    except FaceitNotFoundError:
        await message.answer(
            bold("Player not found."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitUnavailableError:
        await message.answer(
            bold("FACEIT is temporarily unavailable.") + "\nTry again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitRateLimitError:
        await message.answer(
            bold("FACEIT rate limit.") + " Try again shortly.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitAPIError:
        await message.answer(
            bold("FACEIT error.") + " Try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    g = extract_cs2_game(p) or {}
    elo = int(g.get("faceit_elo") or 0)
    level = int(g.get("skill_level") or 0)
    tier = level_tier_emoji(level) if level else "❔"
    region = str(g.get("region") or "—")
    country = (p.get("country") or "").upper()
    flg = flag_emoji(country)

    life = (st.get("lifetime") or {}) if isinstance(st, dict) else {}
    if not isinstance(life, dict):
        life = {}
    parsed = parse_lifetime_stats(life)

    wr = parsed["win_rate_pct"]
    wr_s = _fmt_opt(wr, ".1f") + "%" if wr is not None else "N/A"
    kd_s = _fmt_opt(parsed["kd"], ".2f", "N/A")
    hs_s = _fmt_opt(parsed["hs_pct"], ".1f") + "%" if parsed["hs_pct"] is not None else "N/A"
    mp_s = str(int(parsed["matches"])) if parsed["matches"] is not None else "N/A"
    streak_s = (
        str(int(parsed["longest_win_streak"]))
        if parsed["longest_win_streak"] is not None
        else "N/A"
    )

    w, l = parsed.get("wins"), parsed.get("losses")
    wl_s = "—"
    if w is not None and l is not None:
        wl_s = f"{int(w)} : {int(l)}"

    kills_t = _fmt_opt(parsed.get("kills"), ".0f", "—")
    deaths_t = _fmt_opt(parsed.get("deaths"), ".0f", "—")
    ast_t = _fmt_opt(parsed.get("assists"), ".0f", "—")
    rnd_t = _fmt_opt(parsed.get("rounds"), ".0f", "—")
    mvp_t = _fmt_opt(parsed.get("mvps"), ".0f", "—")
    kr_s = _fmt_opt(parsed.get("kr"), ".2f", "—")
    avg_k = _fmt_opt(parsed.get("avg_kills"), ".2f", "—")
    avg_d = _fmt_opt(parsed.get("avg_deaths"), ".2f", "—")

    items = (recent_raw or {}).get("items") or []
    form = recent_form_badge(items, limit=10)
    nick_disp = esc(p.get("nickname") or nick)

    lines: list[str] = [
        section("📊", "CS2 dashboard"),
        f"{tier} <b>{nick_disp}</b> {flg}".rstrip(),
        sep(26),
        section("🎯", "Overview"),
        f"{bold('ELO')} {code(str(elo))}   {bold('Level')} {code(str(level))}",
        f"{bold('Region')} {code(region)}",
        sep(26),
        section("⚔️", "Combat averages"),
        f"{bold('Win rate')} {code(wr_s)}   {bold('K/D')} {code(kd_s)}   {bold('HS%')} {code(hs_s)}",
        f"{bold('K/R')} {code(kr_s)}   {bold('MVPs')} {code(mvp_t)}",
        f"{bold('Avg K')} {code(avg_k)}   {bold('Avg D')} {code(avg_d)}",
        sep(26),
        section("📈", "Totals"),
        f"{bold('Matches')} {code(mp_s)}   {bold('W : L')} {code(wl_s)}   {bold('Best streak')} {code(streak_s)}",
        f"{bold('K / D / A')} {code(kills_t)} / {code(deaths_t)} / {code(ast_t)}   {bold('Rounds')} {code(rnd_t)}",
        sep(26),
        section("🔥", "Recent form"),
        f"{form}",
        italic("🟩 win · 🟥 loss · ⬜ unknown · order = API (usually newest first)"),
    ]

    text = "\n".join(lines)
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=with_navigation(player_links_kb(str(p.get("faceit_url") or ""))),
    )


async def answer_matches_list(
    message: Message,
    db,
    faceit,
    limit: int = 10,
    *,
    actor_telegram_id: int | None = None,
) -> None:
    user = await _need_user(message, db, actor_telegram_id=actor_telegram_id)
    if not user:
        return

    pid = user["faceit_player_id"]
    limit = max(1, min(20, limit))

    try:
        raw, hist = await asyncio.gather(
            faceit.get_player_match_stats(pid, limit=limit, offset=0),
            faceit.get_player_history(pid, limit=limit),
        )
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

    hist_map: dict[str, dict] = {}
    for h in hist.get("items") or []:
        if not isinstance(h, dict):
            continue
        hid = h.get("match_id") or h.get("ID")
        if hid:
            hist_map[str(hid)] = h

    items = raw.get("items") or []
    lines: list[str] = [
        section("📜", f"Last {len(items)} matches"),
        italic("Tap / copy match id, then use /match id for full board"),
        sep(28),
    ]
    for it in items:
        stats = it.get("stats") if isinstance(it, dict) else None
        if not isinstance(stats, dict):
            continue
        row = parse_match_stats_row(stats)
        mid = row.get("match_id")
        if not mid and isinstance(it, dict) and it.get("match_id"):
            mid = str(it["match_id"])
            row["match_id"] = mid

        wl = "🟢" if row["won"] is True else ("🔴" if row["won"] is False else "⚪")
        kd_val = row["kd"]
        kd_s = f"{kd_val:.2f}" if kd_val is not None else "—"
        finished = _fmt_ts(row["finished_at"])
        map_n = row["map"] or "—"

        block_lines = [
            f"{wl} <b>{esc(map_n)}</b>  ·  K/D {code(kd_s)}  ·  {esc(finished)}",
        ]
        extra: list[str] = []
        if mid and mid in hist_map:
            meta = pick_history_meta(hist_map[mid])
            if meta.get("competition"):
                extra.append(esc(str(meta["competition"])[:40]))
            if meta.get("score"):
                extra.append(code(str(meta["score"])))
        if extra:
            block_lines.append(f"   {' · '.join(extra)}")
        if mid:
            block_lines.append(f"   {bold('id')} {code(str(mid))}")
        lines.append("\n".join(block_lines))

    if len(lines) <= 3:
        lines.append(italic("No recent matches returned by FACEIT."))

    chunks = _split_message_lines(lines)
    nav = with_navigation()
    for i, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            parse_mode=ParseMode.HTML,
            reply_markup=nav if i == len(chunks) - 1 else None,
        )


@router.message(Command("stats"))
async def cmd_stats(message: Message, db, faceit) -> None:
    if msg := await _cooldown_block(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return
    await answer_stats_dashboard(message, db, faceit)


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
    await answer_matches_list(message, db, faceit, limit=limit)


_MATCH_ID_RE = re.compile(r"^[a-fA-F0-9\-]{8,}$")


@router.message(Command("match"))
async def cmd_match(message: Message, command: CommandObject, db, faceit) -> None:
    user = await _need_user(message, db)
    if not user:
        return

    mid = (command.args or "").strip()
    if not mid or not _MATCH_ID_RE.match(mid):
        await message.answer(
            f"{bold('Usage')}: {code('/match match_id')}\n"
            f"{italic('Copy the id from /matches.')}",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    my_pid = user["faceit_player_id"]

    meta: dict = {}
    try:
        meta = await faceit.get_match(mid)
    except (FaceitNotFoundError, FaceitAPIError, FaceitUnavailableError, FaceitRateLimitError):
        meta = {}

    try:
        ms = await faceit.get_match_stats(mid)
    except FaceitNotFoundError:
        await message.answer(
            bold("Match not found."),
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

    rows = aggregate_match_scoreboard(ms)
    if not rows:
        await message.answer(
            bold("No scoreboard data for this match."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    team_a, team_b = group_rows_by_team(rows)

    header: list[str] = [
        section("🧾", "Match scoreboard"),
        code(mid),
    ]
    if meta:
        comp = meta.get("competition_name")
        reg = meta.get("region")
        st = meta.get("status")
        if comp:
            header.append(f"{bold('Event')} {italic(str(comp)[:90])}")
        if reg or st:
            bits = []
            if reg:
                bits.append(bold(str(reg)))
            if st:
                bits.append(code(str(st)))
            header.append(" · ".join(bits))
        res = meta.get("results")
        if isinstance(res, dict) and res.get("score"):
            sc = res["score"]
            if isinstance(sc, dict) and sc:
                try:
                    vs = sorted((int(x) for x in sc.values()), reverse=True)
                    if len(vs) >= 2:
                        header.append(f"{bold('Score')} {code(f'{vs[0]} – {vs[1]}')}")
                except (TypeError, ValueError):
                    pass

    def fmt_row(r: dict) -> str:
        hs = r["hs_pct"]
        hs_s = f"{hs:.0f}%" if hs is not None else "—"
        mark = "👉 " if r["player_id"] == my_pid else "   "
        nick_esc = esc(r["nickname"])
        return (
            f"{mark}<b>{nick_esc}</b>  K {code(str(int(r['kills'])))}  "
            f"D {code(str(int(r['deaths'])))}  A {code(str(int(r['assists'])))}  "
            f"HS {code(hs_s)}"
        )

    chunks: list[str] = header + ["", section("👥", "Rosters"), ""]
    if team_a:
        chunks.append(bold("Team A"))
        for r in team_a:
            chunks.append(fmt_row(r))
    if team_b:
        chunks.append("")
        chunks.append(bold("Team B"))
        for r in team_b:
            chunks.append(fmt_row(r))
    if not team_b and team_a:
        chunks.append(italic("Only one faction in payload (hub/scrim or partial data)."))

    text = "\n".join(chunks)
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=with_navigation(match_faceit_kb(meta.get("faceit_url"))),
    )


@router.callback_query(F.data == "nav:stats")
async def cb_nav_stats(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := await _cooldown_block(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    await answer_stats_dashboard(
        callback.message,
        db,
        faceit,
        actor_telegram_id=callback.from_user.id,
    )


@router.callback_query(F.data == "nav:matches")
async def cb_nav_matches(callback: CallbackQuery, db, faceit) -> None:
    if not callback.message:
        await callback.answer()
        return
    if msg := await _cooldown_block(callback.from_user.id):
        await callback.answer(msg[:180], show_alert=True)
        return
    await callback.answer()
    await answer_matches_list(
        callback.message,
        db,
        faceit,
        limit=10,
        actor_telegram_id=callback.from_user.id,
    )
