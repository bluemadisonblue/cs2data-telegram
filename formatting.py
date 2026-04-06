"""Shared presentation helpers: sections, flags, recent form (MarkdownV2-safe patterns)."""

from __future__ import annotations

from faceit_api import parse_match_stats_row


def flag_emoji(country_code: str | None) -> str:
    """ISO 3166-1 alpha-2 → regional indicator flag emoji."""
    if not country_code or len(country_code) != 2:
        return ""
    c = country_code.upper()
    if not c.isalpha():
        return ""
    return "".join(chr(ord(x) - ord("A") + 0x1F1E6) for x in c)


def md_separator(width: int = 22) -> str:
    """Thin separator line (monospace) for visual grouping."""
    return f"`{'·' * width}`"


def md_section_title(emoji: str, title: str) -> str:
    """Bold section header; title must be pre-escaped if dynamic."""
    return f"{emoji} *{title}*"


def recent_form_badge(items: list[dict], limit: int = 8) -> str:
    """Build win/loss strip from games/cs2/stats items (emoji = quick scan)."""
    chars: list[str] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        stats = it.get("stats")
        if not isinstance(stats, dict):
            continue
        row = parse_match_stats_row(stats)
        if row["won"] is True:
            chars.append("🟩")
        elif row["won"] is False:
            chars.append("🟥")
        else:
            chars.append("⬜")
    if not chars:
        return "—"
    return " ".join(chars)


def format_score_from_history(results: dict | None) -> str | None:
    """Turn MatchResult.score map into '13–10' style string."""
    if not results or not isinstance(results, dict):
        return None
    sc = results.get("score")
    if not isinstance(sc, dict) or not sc:
        return None
    try:
        vals = sorted(
            (int(v) for v in sc.values() if v is not None),
            reverse=True,
        )
    except (TypeError, ValueError):
        return None
    if len(vals) >= 2:
        return f"{vals[0]}–{vals[1]}"
    return None


def pick_history_meta(item: dict) -> dict[str, str | None]:
    """Extract display fields from MatchHistory item."""
    mid = item.get("match_id") or item.get("ID")
    comp = item.get("competition_name") or item.get("competition_id")
    mode = item.get("game_mode") or item.get("match_type")
    results = item.get("results") if isinstance(item.get("results"), dict) else None
    score_s = None
    if results:
        score_s = format_score_from_history(results)
    return {
        "match_id": str(mid) if mid else None,
        "competition": str(comp) if comp else None,
        "mode": str(mode) if mode else None,
        "score": score_s,
    }
