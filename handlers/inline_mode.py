"""Telegram inline queries: paste a FACEIT stats dashboard from any chat (@Bot nickname)."""

from __future__ import annotations

import hashlib
import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from config import INLINE_STATS_MIN_QUERY_LEN
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from stats_format import fetch_stats_bundle, format_stats_dashboard_html

router = Router(name="inline")

logger = logging.getLogger(__name__)

_HELP_ARTICLE_ID = "inline-stats-help"


def _help_article() -> InlineQueryResultArticle:
    text = (
        "<b>FACEIT stats in any chat</b>\n\n"
        "Type <code>@YourBotName</code> and a FACEIT nickname, then tap a result to "
        "insert the CS2 dashboard.\n\n"
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
