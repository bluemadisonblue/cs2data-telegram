"""Inline keyboards and navigation (paired with HTML parse mode).

Context-aware keyboards keep the button count low per screen so mobile
users aren't overwhelmed.  Each stat command gets 3-4 relevant shortcuts
instead of the full 9-button main menu.  The full main menu is still shown
from /start, /help, and /about.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from faceit_api import faceit_match_url

# ---------------------------------------------------------------------------
# Shared button definitions (reused across multiple keyboards)
# ---------------------------------------------------------------------------
_BTN_STATS    = InlineKeyboardButton(text="📊 Stats",   callback_data="nav:stats")
_BTN_PROFILE  = InlineKeyboardButton(text="👤 Profile", callback_data="nav:profile")
_BTN_MATCHES  = InlineKeyboardButton(text="📜 Matches", callback_data="nav:matches")
_BTN_RANK     = InlineKeyboardButton(text="🏆 Rank",    callback_data="nav:rank")
_BTN_MAPS     = InlineKeyboardButton(text="🗺 Maps",    callback_data="nav:maps")
_BTN_COMPARE  = InlineKeyboardButton(text="⚔️ Compare", callback_data="nav:compare")
_BTN_REGISTER = InlineKeyboardButton(text="🔗 Register",callback_data="menu:register")
_BTN_ABOUT    = InlineKeyboardButton(text="ℹ️ About",   callback_data="menu:about")
_BTN_HELP     = InlineKeyboardButton(text="❓ Help",    callback_data="menu:help")
_BTN_HOME     = InlineKeyboardButton(text="🏠 Home",    callback_data="nav:home")
# Opens the composer with @bot + inline query in this chat (DM-friendly).
_BTN_INLINE   = InlineKeyboardButton(
    text="🔎 Inline @bot…",
    switch_inline_query_current_chat=" ",
)


# ---------------------------------------------------------------------------
# Full main menu — for /start, /help, /about, /register flows
# ---------------------------------------------------------------------------

def main_menu_kb() -> InlineKeyboardMarkup:
    """Primary navigation — used for /start, /help, /about, and home."""
    b = InlineKeyboardBuilder()
    b.row(_BTN_STATS, _BTN_PROFILE)
    b.row(_BTN_MATCHES, _BTN_RANK)
    b.row(_BTN_COMPARE, _BTN_HELP)
    b.row(_BTN_ABOUT)
    b.row(_BTN_INLINE)
    b.row(_BTN_HOME)
    return b.as_markup()


def register_success_kb() -> InlineKeyboardMarkup:
    """After linking: pinned ⭐ My stats + same shortcuts as the main menu."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="⭐ My stats", callback_data="nav:stats"),
        _BTN_PROFILE,
    )
    b.row(_BTN_MATCHES, _BTN_RANK)
    b.row(_BTN_COMPARE, _BTN_HELP)
    b.row(_BTN_ABOUT)
    b.row(_BTN_INLINE)
    b.row(_BTN_HOME)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Context-aware compact keyboards (3-4 buttons each)
# ---------------------------------------------------------------------------

def _ctx(url_markup: InlineKeyboardMarkup | None, *rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Build a keyboard: optional URL row(s) → then the provided button rows."""
    b = InlineKeyboardBuilder()
    if url_markup and url_markup.inline_keyboard:
        for row in url_markup.inline_keyboard:
            b.row(*row)
    for row in rows:
        b.row(*row)
    return b.as_markup()


def ctx_stats_kb(url_markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """After /stats: Matches · Rank / Compare · Inline / Home."""
    return _ctx(
        url_markup,
        [_BTN_MATCHES, _BTN_RANK],
        [_BTN_COMPARE, _BTN_INLINE],
        [_BTN_HOME],
    )


def ctx_rank_kb(url_markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """After /rank: Stats · Matches / Home."""
    return _ctx(url_markup, [_BTN_STATS, _BTN_MATCHES], [_BTN_HOME])


def ctx_profile_kb(url_markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """After /profile: Stats · Rank / Home."""
    return _ctx(url_markup, [_BTN_STATS, _BTN_RANK], [_BTN_HOME])


def ctx_matches_kb() -> InlineKeyboardMarkup:
    """After /matches list: Stats · Rank / Home."""
    return _ctx(None, [_BTN_STATS, _BTN_RANK], [_BTN_HOME])


def ctx_maps_kb() -> InlineKeyboardMarkup:
    """After /maps: Stats · Matches / Home."""
    return _ctx(None, [_BTN_STATS, _BTN_MATCHES], [_BTN_HOME])


def ctx_compare_kb() -> InlineKeyboardMarkup:
    """After /compare or /party: Stats · Matches / Inline · Home."""
    return _ctx(None, [_BTN_STATS, _BTN_MATCHES], [_BTN_INLINE, _BTN_HOME])


def ctx_scoreboard_kb(url_markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """After a single match scoreboard: Matches · Home."""
    return _ctx(url_markup, [_BTN_MATCHES, _BTN_HOME])


# ---------------------------------------------------------------------------
# Fallback full navigation (kept for error messages / unregistered users)
# ---------------------------------------------------------------------------

def with_navigation(url_markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """Optional URL row(s) + full main menu.  Use for error / unregistered states."""
    b = InlineKeyboardBuilder()
    if url_markup and url_markup.inline_keyboard:
        for row in url_markup.inline_keyboard:
            b.row(*row)
    for row in main_menu_kb().inline_keyboard:
        b.row(*row)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Matches pagination keyboard
# ---------------------------------------------------------------------------

def matches_pagination_kb(page: int, total_pages: int, limit: int) -> InlineKeyboardMarkup | None:
    """◀ Prev  [page/total]  Next ▶  — omitted when only 1 page."""
    if total_pages <= 1:
        return None
    b = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    if page > 1:
        row.append(InlineKeyboardButton(text="◀ Prev", callback_data=f"matches:p:{page - 1}:{limit}"))
    row.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        row.append(InlineKeyboardButton(text="Next ▶", callback_data=f"matches:p:{page + 1}:{limit}"))
    b.row(*row)
    return b.as_markup()


def with_match_boards_and_nav(
    boards: InlineKeyboardMarkup | None,
    pagination: InlineKeyboardMarkup | None = None,
) -> InlineKeyboardMarkup:
    """Match scoreboard shortcuts + optional pagination + compact context nav."""
    b = InlineKeyboardBuilder()
    if boards and boards.inline_keyboard:
        for row in boards.inline_keyboard:
            b.row(*row)
    if pagination and pagination.inline_keyboard:
        for row in pagination.inline_keyboard:
            b.row(*row)
    # Compact context nav for matches screen
    b.row(_BTN_STATS, _BTN_RANK)
    b.row(_BTN_HOME)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Link / confirm keyboards
# ---------------------------------------------------------------------------

def match_faceit_kb(faceit_url: str | None) -> InlineKeyboardMarkup | None:
    """Match page on FACEIT (same primary label as player profile links)."""
    if not (faceit_url or "").strip():
        return None
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🌐 Open on FACEIT", url=faceit_url))
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
        InlineKeyboardButton(text="❌ Cancel",      callback_data="reg:cancel"),
    )
    return b.as_markup()


def unlink_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Unlink", callback_data="unlink:confirm"),
        InlineKeyboardButton(text="❌ Keep",   callback_data="unlink:cancel"),
    )
    return b.as_markup()


def match_boards_kb(entries: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    """
    Per match: scoreboard (callback) + FACEIT URL (same order as the list above).
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
        url = faceit_match_url(mid)
        if url:
            b.row(
                InlineKeyboardButton(text=text, callback_data=f"m:{mid}"),
                InlineKeyboardButton(text="🌐 Open", url=url),
            )
        else:
            b.row(InlineKeyboardButton(text=text, callback_data=f"m:{mid}"))
    return b.as_markup()
