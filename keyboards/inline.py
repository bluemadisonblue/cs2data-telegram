"""Inline keyboards and navigation (paired with HTML parse mode)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    """Primary navigation — callbacks run real flows where possible."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📊 Stats", callback_data="nav:stats"),
        InlineKeyboardButton(text="👤 Profile", callback_data="nav:profile"),
    )
    b.row(
        InlineKeyboardButton(text="📜 Matches", callback_data="nav:matches"),
        InlineKeyboardButton(text="🏆 Rank", callback_data="nav:rank"),
    )
    b.row(
        InlineKeyboardButton(text="🗺 Maps", callback_data="nav:maps"),
        InlineKeyboardButton(text="⚔️ Compare", callback_data="nav:compare"),
    )
    b.row(
        InlineKeyboardButton(text="🔗 Register", callback_data="menu:register"),
        InlineKeyboardButton(text="❓ Help", callback_data="menu:help"),
    )
    b.row(InlineKeyboardButton(text="🏠 Home", callback_data="nav:home"))
    return b.as_markup()


def with_navigation(url_markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """Optional URL row(s) + full main menu so users are never stuck."""
    b = InlineKeyboardBuilder()
    if url_markup and url_markup.inline_keyboard:
        for row in url_markup.inline_keyboard:
            b.row(*row)
    for row in main_menu_kb().inline_keyboard:
        b.row(*row)
    return b.as_markup()


def with_match_boards_and_nav(boards: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup:
    """Match scoreboard shortcuts + main menu (matches list)."""
    b = InlineKeyboardBuilder()
    if boards and boards.inline_keyboard:
        for row in boards.inline_keyboard:
            b.row(*row)
    for row in main_menu_kb().inline_keyboard:
        b.row(*row)
    return b.as_markup()


def match_faceit_kb(faceit_url: str | None) -> InlineKeyboardMarkup | None:
    if not faceit_url:
        return None
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🌐 Match on FACEIT", url=faceit_url))
    return b.as_markup()


def player_links_kb(faceit_url: str | None) -> InlineKeyboardMarkup | None:
    if not faceit_url:
        return None
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🌐 Open on FACEIT", url=faceit_url))
    return b.as_markup()


def register_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Yes, update", callback_data="reg:confirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="reg:cancel"),
    )
    return b.as_markup()


def unlink_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Unlink", callback_data="unlink:confirm"),
        InlineKeyboardButton(text="❌ Keep", callback_data="unlink:cancel"),
    )
    return b.as_markup()


def match_boards_kb(entries: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    """
    One full-width button per match (same order as the list above).
    callback_data m:{match_id} (must stay ≤64 bytes). Button text ≤64 chars.
    """
    rows = [(mid, lab) for mid, lab in entries if mid and len(f"m:{mid}") <= 64]
    if not rows:
        return None
    b = InlineKeyboardBuilder()
    for mid, lab in rows:
        text = lab.strip() if lab else "·"
        if len(text) > 64:
            text = text[:63] + "…"
        b.row(InlineKeyboardButton(text=text, callback_data=f"m:{mid}"))
    return b.as_markup()
