"""Compare 2–N FACEIT players in one table (/party)."""

from __future__ import annotations

import asyncio
import html as html_mod
import shlex
import time

from aiogram import Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import COOLDOWN_SEC, PARTY_MAX_PLAYERS
from faceit_api import FaceitAPIError, FaceitNotFoundError, FaceitRateLimitError, FaceitUnavailableError
from .compare import fetch_bundle_for_nickname
from keyboards.inline import ctx_compare_kb, with_navigation
from ui_text import bold, code, italic, section

router = Router(name="party")

_last: dict[int, float] = {}


async def _cooldown(user_id: int) -> str | None:
    now = time.monotonic()
    prev = _last.get(user_id)
    if prev is not None and (now - prev) < COOLDOWN_SEC:
        left = COOLDOWN_SEC - (now - prev)
        return f"Wait ~{left:.0f}s before running party again."
    _last[user_id] = now
    return None


def _party_pre_table(bundles: list[dict]) -> str:
    """Monospace table: one row per stat, one column per player."""
    w = 11
    ncols = len(bundles)

    def col(s: str) -> str:
        s = str(s).replace("\n", " ")
        return (s[: w - 1] + "…") if len(s) >= w else s.ljust(w)

    header = col("Stat") + "".join(col(b["nickname"]) for b in bundles)
    sep_ln = "─" * (w * (ncols + 1))

    def line(label: str, cells: list[str]) -> str:
        return col(label) + "".join(col(c) for c in cells)

    rows = [
        header,
        sep_ln,
        line("ELO", [str(b["elo"]) for b in bundles]),
        line("Level", [str(b["level"]) for b in bundles]),
        line("Matches", [b["matches"] for b in bundles]),
        line("W/L", [b["wl"] for b in bundles]),
        line("Win%", [b["wr"] for b in bundles]),
        line("K/D", [b["kd"] for b in bundles]),
        line("K/R", [b["kr"] for b in bundles]),
        line("HS%", [b["hs"] for b in bundles]),
        line("MVPs", [b["mvps"] for b in bundles]),
    ]
    return "<pre>" + html_mod.escape("\n".join(rows)) + "</pre>"


@router.message(Command("party"))
async def cmd_party(message: Message, command: CommandObject, faceit) -> None:
    if msg := await _cooldown(message.from_user.id):
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=with_navigation())
        return

    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            f"{bold('Usage')}: {code('/party nick1 nick2')} … up to {PARTY_MAX_PLAYERS} players\n"
            f"{italic('Example:')} {code('/party s1mple zywoo ropz')}\n"
            f"{italic('Use quotes for spaces:')} {code('/party \"nick one\" nick2')}",
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
        return

    try:
        nicks = shlex.split(raw)
    except ValueError:
        nicks = raw.split()
    if len(nicks) < 2:
        await message.answer(
            bold("Need at least two nicknames.") + f"\n{code('/party nick1 nick2')}",
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
        return

    if len(nicks) > PARTY_MAX_PLAYERS:
        await message.answer(
            bold(f"Too many players (max {PARTY_MAX_PLAYERS})."),
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
        return

    if message.bot:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    loading = await message.answer(
        f"⏳ Loading {len(nicks)} players…",
        parse_mode=ParseMode.HTML,
    )

    tasks = [asyncio.create_task(fetch_bundle_for_nickname(faceit, n)) for n in nicks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    bundles: list[dict] = []
    errors: list[str] = []
    for nick, res in zip(nicks, results):
        if isinstance(res, FaceitNotFoundError):
            errors.append(f"{nick}: not found")
        elif isinstance(res, (FaceitAPIError, FaceitUnavailableError, FaceitRateLimitError)):
            errors.append(f"{nick}: API error")
        elif isinstance(res, Exception):
            errors.append(f"{nick}: error")
        else:
            bundles.append(res)

    await loading.delete()

    if len(bundles) < 2:
        err_txt = "\n".join(errors) if errors else "Unknown failure"
        await message.answer(
            bold("Could not load enough players.") + f"\n<pre>{html_mod.escape(err_txt)}</pre>",
            parse_mode=ParseMode.HTML,
            reply_markup=ctx_compare_kb(),
        )
        return

    body = _party_pre_table(bundles)
    extra = ""
    if errors:
        extra = f"\n{italic('Skipped: ' + ', '.join(errors))}"

    header = (
        f"{section('👥', 'Party compare')}\n"
        f"{italic(str(len(bundles)) + ' players · CS2 FACEIT stats')}\n"
    )
    await message.answer(
        header + body + extra,
        parse_mode=ParseMode.HTML,
        reply_markup=ctx_compare_kb(),
    )
