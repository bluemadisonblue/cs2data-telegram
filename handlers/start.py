"""Welcome, help, home — HTML + main menu."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from keyboards.inline import main_menu_kb
from ui_text import bold, bullet_line, code, italic, section, sep

router = Router(name="start")

HELP_HTML = "\n".join(
    [
        section("📋", "Commands"),
        "",
        bullet_line(f"{bold('Start')} — {italic('menu & shortcuts')}"),
        bullet_line(f"{code('/register nickname')} — link FACEIT"),
        bullet_line(f"{code('/profile')} — avatar & account card"),
        bullet_line(f"{code('/stats')} — full CS2 dashboard"),
        bullet_line(f"{code('/matches')} or {code('/matches 15')} — history"),
        bullet_line(f"{code('/match id')} — scoreboard"),
        bullet_line(f"{code('/rank')} / {code('/elo')} — ELO progress"),
        bullet_line(f"{code('/compare nickname')} — side-by-side table"),
        bullet_line(f"{code('/help')} — this list"),
        "",
        sep(20),
        italic("Tip: use the bottom buttons — they run the same actions."),
    ]
)

WELCOME_HTML = "\n".join(
    [
        section("🎮", "CS2 · FACEIT Stats"),
        "",
        bold("Track ELO, matches, and compare with friends — without leaving Telegram."),
        "",
        f"1️⃣ {code('/register your_faceit_nickname')}",
        f"2️⃣ Open {bold('Stats')}, {bold('Matches')}, or {bold('Rank')} from the keyboard",
        "",
        italic("Commands still work anytime."),
    ]
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        WELCOME_HTML,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        HELP_HTML,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "menu:help")
async def cb_menu_help(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            HELP_HTML,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:register")
async def cb_menu_register(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            f"{bold('Register')}\n"
            f"Send {code('/register your_faceit_nickname')} in this chat.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "nav:home")
async def cb_nav_home(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            WELCOME_HTML,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    await callback.answer()
