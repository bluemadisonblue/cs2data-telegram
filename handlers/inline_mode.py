"""Telegram inline queries: paste a FACEIT stats dashboard from any chat (@Bot nickname)."""

from __future__ import annotations

import asyncio
import html
import hashlib
import logging
import re
import unicodedata

from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

import time

from cache import TTLCache
from config import INLINE_STATS_MIN_QUERY_LEN, PARTY_MAX_PLAYERS
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from faceit_messages import html_faceit_transport_error
from handlers.compare import fetch_bundle_for_nickname
from stats_format import fetch_stats_bundle, format_stats_dashboard_html

router = Router(name="inline")

logger = logging.getLogger(__name__)

# Per-user inline cooldown: prevents API hammering from auto-typing in large chats.
# Single-player lookup: 3 s; multi-player compare: 5 s.
_INLINE_COOLDOWN_SINGLE = 3.0
_INLINE_COOLDOWN_MULTI  = 5.0
_inline_store: TTLCache = TTLCache(maxsize=10_000)


def _inline_check_cooldown(user_id: int, multi: bool = False) -> bool:
    """Return True (blocked) if the user is within the inline cooldown window."""
    key = f"il:{user_id}"
    limit = _INLINE_COOLDOWN_MULTI if multi else _INLINE_COOLDOWN_SINGLE
    now = time.monotonic()
    prev = _inline_store.get(key, limit)
    if prev is not None and (now - prev) < limit:
        return True
    _inline_store.set(key, now)
    return False


async def _inline_typing(inline_query: InlineQuery) -> None:
    """Typing indicator in the user's private chat with the bot (best-effort for inline latency)."""
    bot = inline_query.bot
    if not bot:
        return
    try:
        await bot.send_chat_action(
            chat_id=inline_query.from_user.id,
            action=ChatAction.TYPING,
        )
    except Exception:
        pass

_HELP_ARTICLE_ID = "inline-stats-help"
# Split on " vs ", "versus", " v ", "nick1vs nick2", or "nick1v nick2" (attached v).
# Include Cyrillic «в» (same key as Latin v on RU layout).
_VS_SPLIT_RE = re.compile(
    r"(?:\s+(?:versus|vs\.?|[v\u0432])\s+|(?<=\S)(?:versus|vs\.?|[v\u0432])\s+)",
    re.IGNORECASE,
)


def _normalize_inline_query(q: str) -> str:
    """NFKC + collapse whitespace (handles fullwidth / compatibility chars)."""
    q = unicodedata.normalize("NFKC", (q or "").strip())
    return " ".join(q.split())


def _is_vs_separator_token(tok: str) -> bool:
    """Latin vs / v / versus, or Cyrillic «вс» often typed instead of Latin «vs»."""
    if not tok:
        return False
    t = tok.casefold()
    if t in ("vs", "v", "versus"):
        return True
    # Cyrillic ve (often typed instead of Latin v)
    if t == "в":
        return True
    # Cyrillic small ve + es (looks like "vs" on RU layout)
    return t == "вс"


def _try_parse_vs_tokens(q: str) -> list[str] | None:
    """Whitespace token split — robust when regex misses (mixed scripts, odd spaces)."""
    tokens = q.split()
    if len(tokens) < 3:
        return None
    sep_idx = [i for i, t in enumerate(tokens) if _is_vs_separator_token(t)]
    if not sep_idx:
        return None
    parts: list[str] = []
    start = 0
    for sep_i in sep_idx:
        chunk = tokens[start:sep_i]
        if chunk:
            parts.append(" ".join(chunk))
        start = sep_i + 1
    if start < len(tokens):
        tail = " ".join(tokens[start:])
        if tail:
            parts.append(tail)
    return parts if len(parts) >= 2 else None


def _looks_like_compare_intent(q: str) -> bool:
    """Heuristic: user meant compare but we could not split (wrong script, etc.)."""
    if not q:
        return False
    cf = q.casefold()
    if " vs " in cf or cf.startswith("vs ") or cf.endswith(" vs"):
        return True
    if " v " in cf or cf.endswith(" v"):
        return True
    if " versus " in cf:
        return True
    # Cyrillic «вс» as two letters (common vs misfire)
    if " вс " in q:
        return True
    if "|" in q:
        return True
    tokens = q.split()
    if len(tokens) >= 3 and _is_vs_separator_token(tokens[1]):
        return True
    return False


def _help_article() -> InlineQueryResultArticle:
    text = (
        "<b>FACEIT stats in any chat</b>\n\n"
        "Type <code>@YourBotName</code> and a FACEIT nickname, then tap a result to "
        "insert the CS2 dashboard.\n"
        "Compare: <code>@YourBotName unaidy v baler1on</code> (spaces around "
        "<code>v</code>), or <code>unaidy|baler1on</code>, or "
        "<code>nick1 vs nick2</code> / <code>nick1vs nick2</code>.\n\n"
        "<i>Same data as /stats nickname — no registration.</i>"
    )
    return InlineQueryResultArticle(
        id=_HELP_ARTICLE_ID,
        title="Inline stats",
        description="Type @bot + nickname, pick a result",
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode="HTML",
        ),
    )


def _party_pre_table(bundles: list[dict]) -> str:
    """Inline-friendly compact party table."""
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
    return "<pre>" + html.escape("\n".join(rows)) + "</pre>"


def _try_parse_pipe_query(q: str) -> list[str] | None:
    """nick1|nick2 — no spaces, works on any keyboard layout (recommended for compare)."""
    if "|" not in q:
        return None
    parts = [p.strip() for p in q.split("|") if p.strip()]
    return parts if len(parts) >= 2 else None


def _try_parse_vs_query(q: str) -> list[str] | None:
    q = _normalize_inline_query(q)
    if not q:
        return None
    parts: list[str] | None = _try_parse_pipe_query(q)
    if parts is None:
        parts = [p.strip() for p in _VS_SPLIT_RE.split(q) if p.strip()]
    if len(parts) < 2:
        token_parts = _try_parse_vs_tokens(q)
        if not token_parts:
            return None
        parts = [p.strip() for p in token_parts if p.strip()]
    if len(parts) < 2:
        return None
    # De-dup exact repeats while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        key = p.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique if len(unique) >= 2 else None


def _inline_title(s: str, max_len: int = 64) -> str:
    """Telegram inline result titles are capped (typically 64 chars); truncate safely."""
    s = s.strip()
    if len(s) <= max_len:
        return s
    if max_len < 1:
        return ""
    return s[: max_len - 1] + "…"


def _compare_format_help_article() -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id="inline-vs-format",
        title=_inline_title("Compare: nick1|nick2 or nick1 vs nick2"),
        description="Pipe | works on any keyboard",
        input_message_content=InputTextMessageContent(
            message_text=(
                "<b>Inline compare</b>\n\n"
                "<b>Examples:</b> <code>@YourBotName unaidy v baler1on</code> · "
                "<code>unaidy|baler1on</code> · <code>unaidy vs baler1on</code>.\n\n"
                "If the keyboard inserted Cyrillic <code>вс</code> instead of <code>vs</code>, "
                "use <code>|</code> or retype <code>vs</code> in English layout.\n\n"
                "<i>Wait until both nicknames are typed — results update as you type.</i>"
            ),
            parse_mode="HTML",
        ),
    )


@router.inline_query()
async def inline_faceit_stats(inline_query: InlineQuery, faceit) -> None:
    try:
        await _inline_faceit_stats_impl(inline_query, faceit)
    except Exception as exc:
        logger.exception("inline query failed: %s", exc)
        try:
            await inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id="inline-fatal",
                        title="Inline error",
                        description="Tap to see details",
                        input_message_content=InputTextMessageContent(
                            message_text="<b>Something went wrong.</b>\nPlease try again in a moment.",
                            parse_mode="HTML",
                        ),
                    )
                ],
                cache_time=0,
                is_personal=True,
            )
        except Exception:
            logger.exception("inline query could not answer with error article")


async def _inline_faceit_stats_impl(inline_query: InlineQuery, faceit) -> None:
    q_raw = (inline_query.query or "").strip()
    q = _normalize_inline_query(q_raw)

    if len(q) < INLINE_STATS_MIN_QUERY_LEN:
        await inline_query.answer(
            [_help_article()],
            cache_time=120,
            is_personal=True,
        )
        return

    # Inline compare mode: "@bot nick1 vs nick2 [vs nick3 ...]" (also "nick1 v nick2")
    parsed_vs = _try_parse_vs_query(q)
    if parsed_vs:
        nicks = parsed_vs[:PARTY_MAX_PLAYERS]
        if _inline_check_cooldown(inline_query.from_user.id, multi=True):
            return  # silently drop — Telegram will show the previous cached result
        await _inline_typing(inline_query)
        results = await asyncio.gather(
            *(fetch_bundle_for_nickname(faceit, n) for n in nicks),
            return_exceptions=True,
        )

        bundles: list[dict] = []
        errors: list[str] = []
        for nick, res in zip(nicks, results):
            if isinstance(res, FaceitNotFoundError):
                errors.append(f"{nick}: not found")
            elif isinstance(res, FaceitRateLimitError):
                await inline_query.answer(
                    [
                        InlineQueryResultArticle(
                            id="vs-ratelimit",
                            title="FACEIT rate limit",
                            description="Wait a minute, then retry",
                            input_message_content=InputTextMessageContent(
                                message_text=html_faceit_transport_error(res),
                                parse_mode="HTML",
                            ),
                        )
                    ],
                    cache_time=0,
                    is_personal=True,
                )
                return
            elif isinstance(res, (FaceitUnavailableError, FaceitAPIError)):
                logger.warning("inline compare player %s: %s", nick, res)
                errors.append(f"{nick}: API error")
            elif isinstance(res, Exception):
                logger.warning("inline compare player %s: %s", nick, res)
                errors.append(f"{nick}: error")
            else:
                bundles.append(res)

        if len(bundles) < 2:
            err_txt = "\n".join(errors) if errors else "Need at least two valid players."
            await inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id="vs-notfound",
                        title=_inline_title("Could not compare"),
                        description=err_txt[:100],
                        input_message_content=InputTextMessageContent(
                            message_text=f"<b>Could not load enough players</b>\n<pre>{html.escape(err_txt)}</pre>",
                            parse_mode="HTML",
                        ),
                    )
                ],
                cache_time=5,
                is_personal=True,
            )
            return

        body = (
            "<b>👥 Party compare</b>\n"
            f"<i>{html.escape(' vs '.join(nicks))} · CS2 FACEIT</i>\n"
            f"{_party_pre_table(bundles)}"
        )
        if errors:
            body += f"\n<i>Skipped: {html.escape(', '.join(errors))}</i>"
        if len(parsed_vs) > PARTY_MAX_PLAYERS:
            body += (
                f"\n<i>Showing first {PARTY_MAX_PLAYERS} players "
                f"(inline limit).</i>"
            )
        result_id = hashlib.sha256(
            f"vs:{'::'.join(n.casefold() for n in nicks)}".encode("utf-8")
        ).hexdigest()[:64]
        title_left = bundles[0]["nickname"][:18]
        title_right = bundles[1]["nickname"][:18]
        if len(bundles) > 2:
            title = f"{title_left} vs {title_right} +{len(bundles) - 2}"
        else:
            title = f"{title_left} vs {title_right}"
        title = _inline_title(title)
        article = InlineQueryResultArticle(
            id=result_id,
            title=title,
            description=f"{len(bundles)} players · shareable compare table"[:120],
            input_message_content=InputTextMessageContent(
                message_text=body,
                parse_mode="HTML",
            ),
        )
        await inline_query.answer([article], cache_time=30, is_personal=True)
        return

    # Avoid searching FACEIT for "nick1 vs nick2" when vs was not recognized (wrong script, etc.).
    if _looks_like_compare_intent(q):
        await inline_query.answer(
            [_compare_format_help_article()],
            cache_time=0,
            is_personal=True,
        )
        return

    if len(q) > 64:
        q = q[:64]

    if _inline_check_cooldown(inline_query.from_user.id, multi=False):
        return  # silently drop — Telegram will show the previous cached result

    await _inline_typing(inline_query)
    try:
        bundle = await fetch_stats_bundle(faceit, nickname=q)
    except FaceitNotFoundError:
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="notfound",
                    title=f"No FACEIT user “{q[:28]}”",
                    description="Check spelling or try another nickname",
                    input_message_content=InputTextMessageContent(
                        message_text=f"<b>Not found on FACEIT</b>\n<code>{q}</code>",
                        parse_mode="HTML",
                    ),
                )
            ],
            cache_time=0,
            is_personal=True,
        )
        return
    except FaceitRateLimitError as exc:
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="ratelimit",
                    title="FACEIT rate limit",
                    description="Wait a minute, then retry",
                    input_message_content=InputTextMessageContent(
                        message_text=html_faceit_transport_error(exc),
                        parse_mode="HTML",
                    ),
                )
            ],
            cache_time=0,
            is_personal=True,
        )
        return
    except (FaceitUnavailableError, FaceitAPIError) as exc:
        logger.warning("inline stats failed: %s", exc)
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="error",
                    title="FACEIT unavailable",
                    description="Try again later",
                    input_message_content=InputTextMessageContent(
                        message_text=html_faceit_transport_error(exc),
                        parse_mode="HTML",
                    ),
                )
            ],
            cache_time=0,
            is_personal=True,
        )
        return

    dashboard_html = format_stats_dashboard_html(bundle)
    if len(dashboard_html) > 4000:
        dashboard_html = dashboard_html[:3990] + "\n<i>…truncated</i>"

    thumb = bundle.get("player", {}).get("avatar")
    thumb_url = str(thumb) if thumb and str(thumb).startswith("https://") else None

    result_id = hashlib.sha256(q.lower().encode("utf-8")).hexdigest()[:64]
    title = _inline_title(f"{bundle['nickname'][:36]} · ELO {bundle['elo']}")
    desc = f"L{bundle['level']} · K/D {bundle['kd_s']} · WR {bundle['wr_s']}"[:120]

    art_kw: dict = dict(
        id=result_id,
        title=title,
        description=desc,
        input_message_content=InputTextMessageContent(
            message_text=dashboard_html,
            parse_mode="HTML",
        ),
    )
    if thumb_url:
        art_kw["thumbnail_url"] = thumb_url
        art_kw["thumbnail_width"] = 64
        art_kw["thumbnail_height"] = 64

    article = InlineQueryResultArticle(**art_kw)

    await inline_query.answer(
        [article],
        cache_time=45,
        is_personal=True,
    )
