"""Map frequency from recent FACEIT match rows (CS2)."""

from __future__ import annotations

import time
from collections import Counter, defaultdict

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import database as dbmod
from config import COOLDOWN_SEC
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
    parse_match_stats_row,
)
from keyboards.inline import ctx_maps_kb, with_navigation
from ui_text import bold, code, esc, italic, section

router = Router(name="maps")

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

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer("⏳ Fetching map data…")

    try:
        raw = await faceit.get_player_match_stats(pid, limit=limit, offset=0)
    except FaceitNotFoundError:
        await loading.delete()
        await message.answer(
            bold("Player not found."),
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

    items = (raw or {}).get("items") or []
    ctr: Counter[str] = Counter()
    unknown = 0
    # Per-map wins / losses / K-D totals (only rows with a map label)
    pmap: dict[str, dict[str, float]] = defaultdict(
        lambda: {"w": 0.0, "l": 0.0, "k": 0.0, "d": 0.0, "n": 0.0}
    )
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
            name = str(m)
            ctr[name] += 1
            b = pmap[name]
            b["n"] += 1.0
            won = row.get("won")
            if won is True:
                b["w"] += 1.0
            elif won is False:
                b["l"] += 1.0
            k = row.get("kills")
            dth = row.get("deaths")
            if k is not None:
                b["k"] += float(k)
            if dth is not None:
                b["d"] += float(dth)
        else:
            unknown += 1

    nick = esc(user["faceit_nickname"])
    lines: list[str] = [
        section("🗺", "Recent map mix"),
        f"<b>{nick}</b>  ·  {italic(f'last {len(items)} matches')}",
        "",
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

        # Per-map W/L, win%, K/D (aggregated from the same rows)
        lines.append("")
        lines.append(section("📈", "Per-map from this batch"))
        min_decided = 2  # need at least this many W+L to rank best/worst
        ranked: list[tuple[str, float, float, str]] = []
        for name, b in pmap.items():
            w, l = int(b["w"]), int(b["l"])
            decided = w + l
            kd_s = "—"
            if b["d"] > 0:
                kd_s = f"{b['k'] / b['d']:.2f}"
            elif b["k"] > 0:
                kd_s = "inf"
            win_pct: float | None = None
            if decided > 0:
                win_pct = 100.0 * w / decided
            wl_s = f"{w}-{l}" if decided else "—"
            pct_s = f"{win_pct:.0f}%" if win_pct is not None else "—"
            line = (
                f"{esc(name)}  ·  {code(str(int(b['n'])) + '×')}  "
                f"{bold(wl_s)}  {italic(pct_s)}  K/D {code(kd_s)}"
            )
            ranked.append((name, win_pct if win_pct is not None else -1.0, float(decided), line))
        ranked.sort(key=lambda t: (-t[2], t[0]))  # by games played, then name
        for _, __, ___, line in ranked[:10]:
            lines.append(line)
        if len(ranked) > 10:
            lines.append(italic(f"+ {len(ranked) - 10} more map(s)"))

        candidates = [
            (n, wp, dec)
            for n, wp, dec, _ in ranked
            if wp >= 0 and dec >= min_decided
        ]
        if len(candidates) >= 1:
            best = max(candidates, key=lambda x: (x[1], x[2]))
            worst = min(candidates, key=lambda x: (x[1], -x[2]))
            lines.append("")
            if len(candidates) == 1:
                lines.append(
                    f"{bold('Map highlight')} {esc(best[0])} — {code(f'{best[1]:.0f}%')} win rate "
                    f"({italic(f'≥{min_decided} decided games on this map')})"
                )
            else:
                lines.append(
                    f"{bold('Best map')} {esc(best[0])} ({code(f'{best[1]:.0f}%')})  ·  "
                    f"{bold('Worst')} {esc(worst[0])} ({code(f'{worst[1]:.0f}%')})"
                )
        elif pmap:
            lines.append("")
            lines.append(
                italic(
                    f"Not enough decided games per map (need ≥{min_decided} W/L on a map) for best/worst labels."
                )
            )
    if unknown:
        lines.append("")
        lines.append(italic(f"{unknown} row(s) without a map label"))

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=ctx_maps_kb(),
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
    try:
        await callback.message.delete()
    except Exception:
        pass
    await answer_maps_mix(
        callback.message,
        db,
        faceit,
        limit=40,
        actor_telegram_id=callback.from_user.id,
    )
