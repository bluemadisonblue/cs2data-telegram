"""Match alerts (/watch) and ELO history (/trend)."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

import database as dbmod
from config import COOLDOWN_SEC, WATCH_POLL_INTERVAL
from keyboards.inline import with_navigation
from ui_text import bold, code, esc, italic, section, sep

router = Router(name="watch_trend")

logger = logging.getLogger(__name__)

_last_trend: dict[int, float] = {}


async def _trend_cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last_trend.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before refreshing."
    _last_trend[user_id] = now
    return None


def _fmt_time(raw: str) -> str:
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, TypeError):
        return str(raw)[:16]


def _sparkline(elos: list[int]) -> str:
    if len(elos) < 2:
        return ""
    lo, hi = min(elos), max(elos)
    blocks = "▁▂▃▄▅▆▇█"
    if lo == hi:
        return blocks[7] * len(elos)
    out: list[str] = []
    span = max(hi - lo, 1)
    for e in elos:
        idx = int((e - lo) / span * 7)
        idx = max(0, min(7, idx))
        out.append(blocks[idx])
    return "".join(out)


@router.message(Command("watch"))
async def cmd_watch(message: Message, db) -> None:
    try:
        tid = message.from_user.id
        u = await dbmod.get_user(db, tid)
        if not u:
            await message.answer(
                f"{bold('Account not linked')}\n"
                f"Use {code('/register your_faceit_nickname')} first.",
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return

        cur = bool(int(u.get("watching") or 0))
        new = not cur
        await dbmod.set_watching(db, tid, new)

        nick = esc(u.get("faceit_nickname") or "?")
        mins = max(1, WATCH_POLL_INTERVAL // 60)
        if new:
            text = (
                f"{bold('Match alerts ON')}\n"
                f"{nick}\n\n"
                f"I’ll check for new CS2 matches about every {code(str(mins) + ' min')} "
                f"and message you after each game.\n"
                f"{italic('First check only sets a baseline — no ping for that match.')}"
            )
        else:
            text = (
                f"{bold('Match alerts OFF')}\n"
                f"{nick}\n\n"
                f"{italic('Turn on again anytime with')} {code('/watch')}{italic('.')}"
            )

        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
    except Exception as exc:
        logger.exception("cmd_watch failed: %s", exc)
        await message.answer(
            bold("Could not update watch settings.") + "\nTry again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )


@router.message(Command("trend"))
async def cmd_trend(message: Message, db) -> None:
    try:
        if msg := await _trend_cooldown(message.from_user.id):
            await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
            return

        tid = message.from_user.id
        u = await dbmod.get_user(db, tid)
        if not u:
            await message.answer(
                f"{bold('Account not linked')}\n"
                f"Use {code('/register your_faceit_nickname')} first.",
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return

        snaps = await dbmod.get_elo_snapshots(db, tid, limit=20)
        if not snaps:
            await message.answer(
                "\n".join(
                    [
                        section("📈", "ELO trend"),
                        "",
                        bold("No history stored yet."),
                        "",
                        f"{italic('Snapshots are saved when ELO changes after')} {code('/rank')} {italic('or')} {code('/stats')}{italic('.')}",
                        f"{italic('Use those commands over a few days, then try again.')}",
                    ]
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return

        elos = [int(s["elo"]) for s in snaps]
        spark = _sparkline(elos)
        lines: list[str] = [
            section("📈", "ELO trend"),
            f"{bold('Player')} {esc(u.get('faceit_nickname') or '')}",
            sep(24),
        ]
        if spark:
            lines.append(f"{bold('Sparkline')} {code(spark)}")
            lines.append(
                f"{italic('Low')} {code(str(min(elos)))} · {italic('High')} {code(str(max(elos)))}"
            )
            lines.append("")

        lines.append(bold("Recent points (oldest → newest)"))
        for s in snaps[-12:]:
            ra = s.get("recorded_at") or ""
            lines.append(
                f"{code(_fmt_time(str(ra)))}  ·  ELO {code(str(s['elo']))}  ·  L{code(str(s['level']))}"
            )

        if len(snaps) > 12:
            lines.append(italic(f"… +{len(snaps) - 12} older snapshot(s) in DB"))

        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=with_navigation())
    except Exception as exc:
        logger.exception("cmd_trend failed: %s", exc)
        await message.answer(
            bold("Could not load ELO trend.") + "\nTry again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
