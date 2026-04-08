"""Shared FACEIT stats fetch + HTML formatting for /stats and inline mode."""

from __future__ import annotations

from typing import Any

from config import (
    RECENT_FORM_LIMIT,
    STATS_RECENT_WINDOW_MATCHES,
    level_tier_emoji,
)
from faceit_api import (
    FaceitAPI,
    FaceitAPIError,
    current_win_streak,
    extract_cs2_game,
    lifetime_map_from_stats_response,
    parse_lifetime_stats,
    parse_match_stats_row,
)
from formatting import flag_emoji, recent_form_badge
from ui_text import bold, code, esc, italic, section, sep


def aggregate_recent_match_window(
    items: list[Any],
    *,
    limit: int,
) -> dict[str, Any]:
    """Roll WR, K/D, HS, K/R, per-match averages, MVP sum from newest *limit* games."""
    rows: list[dict[str, Any]] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        stats = it.get("stats")
        if not isinstance(stats, dict):
            continue
        rows.append(parse_match_stats_row(stats))

    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "wr_pct": None,
            "kd": None,
            "hs_pct": None,
            "kr": None,
            "avg_k": None,
            "avg_d": None,
            "mvp_sum": None,
        }

    wins = sum(1 for r in rows if r.get("won") is True)
    losses = sum(1 for r in rows if r.get("won") is False)
    decided = wins + losses
    wr_pct = (100.0 * wins / decided) if decided else None

    kills = sum((r.get("kills") or 0.0) for r in rows)
    deaths = sum((r.get("deaths") or 0.0) for r in rows)
    kd = (kills / deaths) if deaths else None
    avg_k = kills / n
    avg_d = deaths / n

    hs_vals = [r["hs_pct"] for r in rows if r.get("hs_pct") is not None]
    hs_pct = sum(hs_vals) / len(hs_vals) if hs_vals else None

    total_rounds = sum((r.get("rounds") or 0.0) for r in rows if r.get("rounds"))
    kr: float | None
    if total_rounds and total_rounds > 0:
        kr = kills / total_rounds
    else:
        kr_vals = [r["kr"] for r in rows if r.get("kr") is not None]
        kr = sum(kr_vals) / len(kr_vals) if kr_vals else None

    mvp_vals = [r["mvps"] for r in rows if r.get("mvps") is not None]
    mvp_sum = sum(mvp_vals) if mvp_vals else None

    return {
        "n": n,
        "wr_pct": wr_pct,
        "kd": kd,
        "hs_pct": hs_pct,
        "kr": kr,
        "avg_k": avg_k,
        "avg_d": avg_d,
        "mvp_sum": mvp_sum,
    }


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

    p, st, recent_raw = await faceit.get_dashboard_bundle(
        pid, STATS_RECENT_WINDOW_MATCHES
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
    win_stats = aggregate_recent_match_window(items, limit=STATS_RECENT_WINDOW_MATCHES)

    if win_stats["n"] > 0:
        recent_label = (
            f"last {win_stats['n']} matches"
            if win_stats["n"] < STATS_RECENT_WINDOW_MATCHES
            else f"last {STATS_RECENT_WINDOW_MATCHES} matches"
        )
        recent_wr_s = (
            _fmt_opt(win_stats["wr_pct"], ".1f") + "%"
            if win_stats["wr_pct"] is not None
            else "N/A"
        )
        recent_kd_s = _fmt_opt(win_stats["kd"], ".2f", "N/A")
        recent_hs_s = (
            _fmt_opt(win_stats["hs_pct"], ".1f") + "%"
            if win_stats["hs_pct"] is not None
            else "N/A"
        )
        recent_kr_s = _fmt_opt(win_stats["kr"], ".2f", "N/A")
        recent_avg_k = _fmt_opt(win_stats["avg_k"], ".2f", "—")
        recent_avg_d = _fmt_opt(win_stats["avg_d"], ".2f", "—")
        if win_stats["mvp_sum"] is not None:
            recent_mvp_s = _fmt_opt(win_stats["mvp_sum"], ".0f", "—")
        else:
            recent_mvp_s = "—"
    else:
        recent_label = "lifetime · no recent matches"
        recent_wr_s, recent_kd_s, recent_hs_s, recent_kr_s = wr_s, kd_s, hs_s, kr_s
        recent_avg_k, recent_avg_d, recent_mvp_s = avg_k, avg_d, mvp_t

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
        "recent_label": recent_label,
        "recent_wr_s": recent_wr_s,
        "recent_kd_s": recent_kd_s,
        "recent_hs_s": recent_hs_s,
        "recent_kr_s": recent_kr_s,
        "recent_avg_k": recent_avg_k,
        "recent_avg_d": recent_avg_d,
        "recent_mvp_s": recent_mvp_s,
        "form": form,
        "recent_form_n": form_n,
        "streak": streak_info,
        "faceit_url": str(p.get("faceit_url") or ""),
    }


def format_stats_dashboard_html(bundle: dict[str, Any]) -> str:
    """HTML body for the /stats dashboard — spaced sections, rolling window stats."""
    nick_disp = esc(bundle["nickname"])
    lines: list[str] = [
        section("📊", "CS2 dashboard"),
        f"{bundle['tier']} <b>{nick_disp}</b> {bundle['flg']}".rstrip(),
        "",
        f"{bold('ELO')} {code(str(bundle['elo']))}   {bold('Level')} {code(str(bundle['level']))}   "
        f"{bold('Region')} {code(bundle['region'])}",
        "",
        sep(24),
        "",
        f"{bold('Stats')}  {italic(bundle['recent_label'])}",
        f"WR {code(bundle['recent_wr_s'])}    "
        f"K/D {code(bundle['recent_kd_s'])}    "
        f"HS {code(bundle['recent_hs_s'])}    "
        f"K/R {code(bundle['recent_kr_s'])}",
        "",
        f"{italic('Per match')}    "
        f"Avg K {code(bundle['recent_avg_k'])}    "
        f"Avg D {code(bundle['recent_avg_d'])}    "
        f"MVPs {code(bundle['recent_mvp_s'])}",
        "",
        sep(24),
        "",
        f"{bold('Totals')}  {italic('(lifetime)')}",
        f"{code(bundle['mp_s'])} matches    W/L {code(bundle['wl_s'])}    "
        f"best streak {code(bundle['streak_s'])}",
        f"{bold('KDA')} {code(bundle['kills_t'])} / {code(bundle['deaths_t'])} / {code(bundle['ast_t'])}    "
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

    return "\n".join(lines)
