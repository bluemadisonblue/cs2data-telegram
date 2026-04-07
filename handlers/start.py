"""Welcome, help, home — HTML + main menu."""

from __future__ import annotations

import os

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import BOT_VERSION, COOLDOWN_SEC, MATCHES_PAGE_SIZE, PARTY_MAX_PLAYERS
from keyboards.inline import main_menu_kb
from ui_text import bold, bullet_line, code, esc, italic, link, section, sep, tip_item

router = Router(name="start")

HELP_HTML = "\n".join(
    [
        section("📋", "Commands"),
        "",
        bullet_line(f"{code('/register nickname')} — link your FACEIT account"),
        bullet_line(f"{code('/unlink')} — remove your FACEIT link"),
        "",
        bullet_line(f"{code('/profile')} — avatar & account card"),
        bullet_line(f"{code('/stats')} — full CS2 dashboard (own account)"),
        bullet_line(f"{code('/stats nickname')} — look up any player by nickname"),
        bullet_line(f"{code('/matches')} or {code('/matches 15')} — recent match history (1–20)"),
        bullet_line(f"{code('/match id')} — full scoreboard for one match"),
        bullet_line(f"{code('/rank')} — ELO progress ({code('/elo')} is the same command)"),
        bullet_line(f"{code('/compare nickname')} — side-by-side stat table (2 players)"),
        bullet_line(f"{code('/party nick1 nick2 …')} — table for up to {PARTY_MAX_PLAYERS} players"),
        bullet_line(f"{code('/leaderboard')} — registered users sorted by live ELO"),
        bullet_line(
            f"Inline: {code('@botname nickname')} (one player), or compare with "
            f"{code('@botname unaidy v baler1on')} / {code('@botname unaidy|baler1on')} / "
            f"{code('@botname unaidy vs baler1on')}."
        ),
        bullet_line(f"{code('/maps')} or {code('/maps 50')} — recent map frequency"),
        bullet_line(f"{code('/trend')} — ELO history over time"),
        bullet_line(f"{code('/watch')} — toggle new-match alerts"),
        bullet_line(f"{code('/version')} — running bot version/build"),
        "",
        bullet_line(f"{code('/about')} — version & data source"),
        bullet_line(f"{code('/help')} — this list"),
        "",
        sep(20),
        italic("Tips:"),
        tip_item(esc("Buttons under messages repeat the same actions as commands.")),
        tip_item(
            esc("/matches lists up to 20 games; "),
            code(str(MATCHES_PAGE_SIZE)),
            esc(" per page — use ◀ / ▶. Row numbers match the scoreboard buttons."),
        ),
        tip_item(
            esc(
                "/stats shows lifetime best win streak in Totals and your current W/L streak "
                "under Recent form when the API returns enough games."
            )
        ),
        tip_item(
            esc("/compare marks the better value per stat with "),
            code("<"),
            esc(" after the winner; ties have no marker."),
        ),
        tip_item(
            code("/profile"),
            esc(", "),
            code("/rank"),
            esc(", "),
            code("/stats"),
            esc(", "),
            code("/matches"),
            esc(", "),
            code("/maps"),
            esc(", and "),
            code("/compare"),
            esc(" share a "),
            code(f"{int(COOLDOWN_SEC)}s"),
            esc(" cooldown."),
        ),
    ]
)

WELCOME_HTML = "\n".join(
    [
        section("🎮", "CS2 · FACEIT Stats"),
        "",
        bold("Track ELO, matches, and compare with friends — without leaving Telegram."),
        "",
        f"1️⃣ {code('/register your_faceit_nickname')}",
        f"2️⃣ Use {bold('Stats')}, {bold('Matches')}, {bold('Rank')}, {bold('Maps')}, or {bold('Compare')} below",
        "",
        italic(
            "Commands still work anytime. Look up anyone with /stats nickname, or inline "
            "@bot + nickname / nick1|nick2 (tap 🔎 Inline @bot… below)."
        ),
    ]
)

ABOUT_HTML = "\n".join(
    [
        section("ℹ️", "About"),
        "",
        f"Version {code(BOT_VERSION)}",
        "",
        "Stats and matches come from the "
        f"{link('https://docs.faceit.com/', 'FACEIT Data API')} "
        "(CS2). This bot is not affiliated with FACEIT.",
        "",
        f"{bold('Contact')} {code('@tyuiqak')} · {code('tyuiqak@gmail.com')}",
        "",
        sep(20),
        italic("1.4: inline @bot stats, /leaderboard, /party, shared stats_format module."),
    ]
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME_HTML, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(
        ABOUT_HTML,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_HTML, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())


@router.message(Command("version"))
async def cmd_version(message: Message) -> None:
    build = (
        (os.getenv("GIT_SHA") or "").strip()
        or (os.getenv("SOURCE_VERSION") or "").strip()
        or "local"
    )
    await message.answer(
        "\n".join(
            [
                section("🧩", "Runtime version"),
                f"{bold('Version')} {code(BOT_VERSION)}",
                f"{bold('Build')} {code(build)}",
            ]
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "menu:help")
async def cb_menu_help(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(HELP_HTML, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:register")
async def cb_menu_register(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(
            f"{bold('Register')}\nSend {code('/register your_faceit_nickname')} in this chat.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "nav:home")
async def cb_nav_home(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(WELCOME_HTML, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
    await callback.answer()
