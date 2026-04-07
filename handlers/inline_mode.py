"""Telegram inline queries: paste a FACEIT stats dashboard from any chat (@Bot nickname)."""

from __future__ import annotations

import asyncio
import html
import hashlib
import logging
import re

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from config import INLINE_STATS_MIN_QUERY_LEN, PARTY_MAX_PLAYERS
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from handlers.compare import fetch_bundle_for_nickname
from stats_format import fetch_stats_bundle, format_stats_dashboard_html

router = Router(name="inline")

logger = logging.getLogger(__name__)

_HELP_ARTICLE_ID = "inline-stats-help"
_VS_SPLIT_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)


def _help_article() -> InlineQueryResultArticle:
    text = (
        "<b>FACEIT stats in any chat</b>\n\n"
        "Type <code>@YourBotName</code> and a FACEIT nickname, then tap a result to "
        "insert the CS2 dashboard.\n"
        "Or use <code>@YourBotName nick1 vs nick2</code> for a shareable compare table.\n\n"
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


def _try_parse_vs_query(q: str) -> list[str] | None:
    parts = [p.strip() for p in _VS_SPLIT_RE.split(q.strip()) if p.strip()]
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


@router.inline_query()
async def inline_faceit_stats(inline_query: InlineQuery, faceit) -> None:
    q_raw = (inline_query.query or "").strip()
    q = " ".join(q_raw.split())

    if len(q) < INLINE_STATS_MIN_QUERY_LEN:
        await inline_query.answer(
            [_help_article()],
            cache_time=120,
            is_personal=True,
        )
        return

    # Inline compare mode: "@bot nick1 vs nick2 [vs nick3 ...]"
    parsed_vs = _try_parse_vs_query(q)
    if parsed_vs:
        nicks = parsed_vs[:PARTY_MAX_PLAYERS]
        try:
            bundles = await asyncio.gather(
                *(fetch_bundle_for_nickname(faceit, n) for n in nicks),
            )
        except FaceitNotFoundError:
            await inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id="vs-notfound",
                        title="Player not found",
                        description="One or more nicknames could not be resolved",
                        input_message_content=InputTextMessageContent(
                            message_text=f"<b>Not found on FACEIT</b>\n<code>{html.escape(' vs '.join(nicks))}</code>",
                            parse_mode="HTML",
                        ),
                    )
                ],
                cache_time=5,
                is_personal=True,
            )
            return
        except FaceitRateLimitError:
            await inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id="vs-ratelimit",
                        title="FACEIT rate limit",
                        description="Try again in a few seconds",
                        input_message_content=InputTextMessageContent(
                            message_text="<b>FACEIT rate limit</b>\nTry again shortly.",
                            parse_mode="HTML",
                        ),
                    )
                ],
                cache_time=0,
                is_personal=True,
            )
            return
        except (FaceitUnavailableError, FaceitAPIError) as exc:
            logger.warning("inline compare failed: %s", exc)
            await inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id="vs-error",
                        title="FACEIT temporarily unavailable",
                        description=str(exc)[:80],
                        input_message_content=InputTextMessageContent(
                            message_text="<b>Could not reach FACEIT</b>\nTry again later.",
                            parse_mode="HTML",
                        ),
                    )
                ],
                cache_time=0,
                is_personal=True,
            )
            return

        body = (
            "<b>👥 Party compare</b>\n"
            f"<i>{html.escape(' vs '.join(nicks))} · CS2 FACEIT</i>\n"
            f"{_party_pre_table(bundles)}"
        )
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

    if len(q) > 64:
        q = q[:64]

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
            cache_time=5,
            is_personal=True,
        )
        return
    except FaceitRateLimitError:
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="ratelimit",
                    title="FACEIT rate limit",
                    description="Try again in a few seconds",
                    input_message_content=InputTextMessageContent(
                        message_text="<b>FACEIT rate limit</b>\nTry again shortly.",
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
                    title="FACEIT temporarily unavailable",
                    description=str(exc)[:80],
                    input_message_content=InputTextMessageContent(
                        message_text="<b>Could not reach FACEIT</b>\nTry again later.",
                        parse_mode="HTML",
                    ),
                )
            ],
            cache_time=0,
            is_personal=True,
        )
        return

    html = format_stats_dashboard_html(bundle)
    if len(html) > 4000:
        html = html[:3990] + "\n<i>…truncated</i>"

    thumb = bundle.get("player", {}).get("avatar")
    thumb_url = str(thumb) if thumb and str(thumb).startswith("http") else None

    result_id = hashlib.sha256(q.lower().encode("utf-8")).hexdigest()[:64]
    title = f"{bundle['nickname'][:36]} · ELO {bundle['elo']}"
    desc = f"L{bundle['level']} · K/D {bundle['kd_s']} · WR {bundle['wr_s']}"[:120]

    art_kw: dict = dict(
        id=result_id,
        title=title,
        description=desc,
        input_message_content=InputTextMessageContent(
            message_text=html,
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
