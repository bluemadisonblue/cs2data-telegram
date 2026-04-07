"""Shared FACEIT stats fetch + HTML formatting for /stats and inline mode."""

from __future__ import annotations

import asyncio
from typing import Any

from config import RECENT_FORM_LIMIT, level_tier_emoji
from faceit_api import (
    FaceitAPI,
    FaceitAPIError,
    current_win_streak,
    extract_cs2_game,
    lifetime_map_from_stats_response,
    parse_lifetime_stats,
)
from formatting import flag_emoji, recent_form_badge
from ui_text import bold, code, esc, italic, section, sep


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

    p, st, recent_raw = await asyncio.gather(
        faceit.get_player_by_id(pid),
        faceit.get_player_stats_lifetime(pid),
        faceit.get_player_match_stats(pid, limit=RECENT_FORM_LIMIT, offset=0),
    )

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
    form = recent_form_badge(items, limit=10)
    streak_info = current_win_streak(items)
    streak_line = ""
    if streak_info is not None:
        is_win, streak_len = streak_info
        streak_emoji = "🟩" if is_win else "🟥"
        streak_label = "W" if is_win else "L"
        streak_line = f"{bold('Current streak')} {code(streak_emoji + ' ' + streak_label + str(streak_len))}"

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
        "streak_line": streak_line,
        "faceit_url": str(p.get("faceit_url") or ""),
    }


def format_stats_dashboard_html(bundle: dict[str, Any]) -> str:
    """HTML body identical to the /stats dashboard message."""
    nick_disp = esc(bundle["nickname"])
    lines: list[str] = [
        section("📊", "CS2 dashboard"),
        f"{bundle['tier']} <b>{nick_disp}</b> {bundle['flg']}".rstrip(),
        sep(26),
        section("🎯", "Overview"),
        f"{bold('ELO')} {code(str(bundle['elo']))}   {bold('Level')} {code(str(bundle['level']))}",
        f"{bold('Region')} {code(bundle['region'])}",
        sep(26),
        section("⚔️", "Combat averages"),
        f"{bold('Win rate')} {code(bundle['wr_s'])}   {bold('K/D')} {code(bundle['kd_s'])}   {bold('HS%')} {code(bundle['hs_s'])}",
        f"{bold('K/R')} {code(bundle['kr_s'])}   {bold('MVPs')} {code(bundle['mvp_t'])}",
        f"{bold('Avg K')} {code(bundle['avg_k'])}   {bold('Avg D')} {code(bundle['avg_d'])}",
        sep(26),
        section("📈", "Totals"),
        f"{bold('Matches')} {code(bundle['mp_s'])}   {bold('W : L')} {code(bundle['wl_s'])}   {bold('Best streak')} {code(bundle['streak_s'])}",
        f"{bold('K / D / A')} {code(bundle['kills_t'])} / {code(bundle['deaths_t'])} / {code(bundle['ast_t'])}   {bold('Rounds')} {code(bundle['rnd_t'])}",
        sep(26),
        section("🔥", "Recent form"),
        bundle["form"],
    ]
    if bundle.get("streak_line"):
        lines.append(bundle["streak_line"])
    lines.append(italic("🟩 win · 🟥 loss · ⬜ unknown · newest first"))
    return "\n".join(lines)
