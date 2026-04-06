"""Telegram HTML formatting (escape-safe, readable on mobile)."""

from __future__ import annotations

import html


def esc(x: str | int | float | None) -> str:
    if x is None:
        return ""
    return html.escape(str(x), quote=False)


def bold(x: str) -> str:
    return f"<b>{esc(x)}</b>"


def italic(x: str) -> str:
    return f"<i>{esc(x)}</i>"


def code(x: str) -> str:
    return f"<code>{esc(x)}</code>"


def section(emoji: str, title: str) -> str:
    return f"{emoji} <b>{esc(title)}</b>"


def sep(width: int = 24) -> str:
    return f"<code>{esc('·' * width)}</code>"


def link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(text)}</a>'


def bullet_line(text: str) -> str:
    return f"• {text}"


def spacer() -> str:
    return ""
