"""Shared FACEIT stats fetch + HTML formatting for /stats and inline mode."""

from __future__ import annotations

from typing import Any

from config import BOT_USERNAME, RECENT_FORM_LIMIT, level_tier_emoji
from faceit_api import (
    FaceitAPI,
    FaceitAPIError,
    current_win_streak,
    extract_cs2_game,
    lifetime_map_from_stats_response,
    parse_lifetime_stats,
)
from formatting import flag_emoji, recent_form_badge
from ui_text import bold, code, esc, italic, link, section


async def fetch_stats_bundle(
    faceit: FaceitAPI,
    *,
    nickname: str | None = None,
    player_id: str | None = None,
) -> dict[str, Any]:
    """Load player + lifetime + recent matches. Exactly one of nickname or player_id required."""
    if (nickname is None) == (player_id is None):
        raise ValueError("pass exactly one of nickname= or player_id=")

    if nickname:
        lookup_pl = await faceit.get_player_by_nickname(nickname.strip())
        pid = lookup_pl.get("player_id")
        if not pid:
            raise FaceitAPIError("no player_id in nickname response")
    else:
        pid = player_id  # type: ignore[assignment]

    p, st, recent_raw = await faceit.get_dashboard_bundle(pid, RECENT_FORM_LIMIT)

    g = extract_cs2_game(p) or {}
    elo = int(g.get("faceit_elo") or 0)
    level = int(g.get("skill_level") or 0)
    tier = level_tier_emoji(level) if level else "❔"
    region = str(g.get("region") or "—")
    country = (p.get("country") or "").upper()
    flg = flag_emoji(country)

    life = lifetime_map_from_stats_response(st if isinstance(st, dict) else None)
    parsed = parse_lifetime_stats(life)

    def _fmt_opt(v: float | None, fmt: str, fallback: str = "—") -> str:
        if v is None:
            return fallback
        try:
            return format(float(v), fmt)
        except (TypeError, ValueError):
            return fallback

    wr = parsed["win_rate_pct"]
    wr_s = _fmt_opt(wr, ".1f") + "%" if wr is not None else "N/A"
    kd_s = _fmt_opt(parsed["kd"], ".2f", "N/A")
    hs_s = _fmt_opt(parsed["hs_pct"], ".1f") + "%" if parsed["hs_pct"] is not None else "N/A"
    mp_s = str(int(parsed["matches"])) if parsed["matches"] is not None else "N/A"
    streak_s = (
        str(int(parsed["longest_win_streak"]))
        if parsed["longest_win_streak"] is not None
        else "N/A"
    )
    w, l = parsed.get("wins"), parsed.get("losses")
    wl_s = f"{int(w)} : {int(l)}" if w is not None and l is not None else "—"
    kills_t = _fmt_opt(parsed.get("kills"), ".0f", "—")
    deaths_t = _fmt_opt(parsed.get("deaths"), ".0f", "—")
    ast_t = _fmt_opt(parsed.get("assists"), ".0f", "—")
    rnd_t = _fmt_opt(parsed.get("rounds"), ".0f", "—")
    mvp_t = _fmt_opt(parsed.get("mvps"), ".0f", "—")
    kr_s = _fmt_opt(parsed.get("kr"), ".2f", "—")
    avg_k = _fmt_opt(parsed.get("avg_kills"), ".2f", "—")
    avg_d = _fmt_opt(parsed.get("avg_deaths"), ".2f", "—")

    items = (recent_raw or {}).get("items") or []
    form, form_n = recent_form_badge(items, limit=min(10, RECENT_FORM_LIMIT))
    streak_info = current_win_streak(items)

    nick_raw = str(p.get("nickname") or nickname or "?")

    return {
        "player": p,
        "nickname": nick_raw,
        "elo": elo,
        "level": level,
        "tier": tier,
        "region": region,
        "flg": flg,
        "wr_s": wr_s,
        "kd_s": kd_s,
        "hs_s": hs_s,
        "mp_s": mp_s,
        "streak_s": streak_s,
        "wl_s": wl_s,
        "kills_t": kills_t,
        "deaths_t": deaths_t,
        "ast_t": ast_t,
        "rnd_t": rnd_t,
        "mvp_t": mvp_t,
        "kr_s": kr_s,
        "avg_k": avg_k,
        "avg_d": avg_d,
        "form": form,
        "recent_form_n": form_n,
        "streak": streak_info,
        "faceit_url": str(p.get("faceit_url") or ""),
    }


def _stats_share_watermark_html(bot_username: str) -> str:
    """Footer for screenshots: subtle CTA + t.me link to the bot."""
    u = (bot_username or "").strip().lstrip("@")
    if not u:
        return ""
    return italic("Clip & share — ") + link(f"https://t.me/{u}", f"@{u}")


def format_stats_dashboard_html(
    bundle: dict[str, Any],
    *,
    bot_username: str | None = None,
) -> str:
    """HTML body for the /stats dashboard — compact, no decorative separators."""
    nick_disp = esc(bundle["nickname"])
    lines: list[str] = [
        section("📊", "CS2 dashboard"),
        f"{bundle['tier']} <b>{nick_disp}</b> {bundle['flg']}".rstrip(),
        "",
        f"{bold('ELO')} {code(str(bundle['elo']))}   {bold('Level')} {code(str(bundle['level']))}   "
        f"{bold('Region')} {code(bundle['region'])}",
        "",
        f"{bold('Combat')}  WR {code(bundle['wr_s'])}  ·  K/D {code(bundle['kd_s'])}  ·  "
        f"HS {code(bundle['hs_s'])}  ·  K/R {code(bundle['kr_s'])}",
        f"{bold('Per game')}  Avg K {code(bundle['avg_k'])}  ·  Avg D {code(bundle['avg_d'])}  ·  "
        f"MVPs {code(bundle['mvp_t'])}",
        "",
        f"{bold('Totals')}  {code(bundle['mp_s'])} matches  ·  W/L {code(bundle['wl_s'])}  ·  "
        f"best streak {code(bundle['streak_s'])}",
        f"{bold('KDA')} {code(bundle['kills_t'])} / {code(bundle['deaths_t'])} / {code(bundle['ast_t'])}   "
        f"{bold('Rounds')} {code(bundle['rnd_t'])}",
        "",
        section("🔥", "Form"),
    ]
    form_raw = bundle["form"]
    n_show = int(bundle.get("recent_form_n") or 0)
    if form_raw == "—" or n_show == 0:
        lines.append(italic("No recent matches in this API batch."))
    else:
        lines.append(f"<code>{form_raw}</code>")

    streak = bundle.get("streak")
    if streak is not None:
        is_win, n = streak[0], streak[1]
        if is_win:
            phrase = f"{n} win in a row" if n == 1 else f"{n} wins in a row"
            mark = "🟢"
        else:
            phrase = f"{n} loss in a row" if n == 1 else f"{n} losses in a row"
            mark = "🔴"
        lines.append(f"{bold('Streak')} {mark} {code(phrase)}")

    u = (bot_username or BOT_USERNAME or "").strip().lstrip("@")
    wm = _stats_share_watermark_html(u)
    if wm:
        lines.append("")
        lines.append(wm)

    return "\n".join(lines)
