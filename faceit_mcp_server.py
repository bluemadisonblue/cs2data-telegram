"""FACEIT CS2 MCP server — exposes player stats, match history, comparisons, leaderboard, and ELO trend.

Runs as a stdio MCP server; connect it to Claude Desktop or Claude Code.

Quick start
-----------
1. Install deps:
       pip install mcp aiohttp aiosqlite python-dotenv

2. Add to your Claude Desktop config (claude_desktop_config.json):

       {
         "mcpServers": {
           "faceit-cs2": {
             "command": "python",
             "args": ["C:/path/to/CS2DATA/faceit_mcp_server.py"],
             "env": { "FACEIT_API_KEY": "your_key_here" }
           }
         }
       }

   Or use an existing .env file — the server loads it automatically.

Available tools
---------------
  get_player_stats    — ELO, level, region, lifetime K/D, HS%, win rate …
  get_match_history   — last N matches with map, W/L, K/D, HS%, kills
  compare_players     — side-by-side stats for 2–6 FACEIT nicknames
  get_leaderboard     — all bot-registered users ranked by live ELO
  get_elo_trend       — stored ELO snapshots for a registered nickname
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import aiosqlite
from mcp.server.fastmcp import FastMCP

from cache import TTLCache
from config import DB_PATH, FACEIT_API_KEY
from database import get_watching_users  # reuse schema helpers
from faceit_api import (
    FaceitAPI,
    FaceitAPIError,
    FaceitNotFoundError,
    extract_cs2_game,
    lifetime_map_from_stats_response,
    parse_lifetime_stats,
    parse_match_stats_row,
)

# ---------------------------------------------------------------------------
# Server + shared FACEIT client
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="faceit-cs2",
    instructions=(
        "Provides live CS2 FACEIT data: player stats, match history, player comparisons, "
        "leaderboard, and ELO trend for bot-registered users. "
        "Always call get_player_stats before answering questions about a specific player."
    ),
)

# Module-level singletons — created once when the server starts.
_http: aiohttp.ClientSession | None = None
_faceit: FaceitAPI | None = None


def _api() -> FaceitAPI:
    assert _faceit is not None, "Server not initialised"
    return _faceit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_opt(v: float | None, fmt: str, fallback: str = "N/A") -> str:
    if v is None:
        return fallback
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return fallback


async def _bundle_for_nickname(nickname: str) -> dict[str, Any]:
    """Resolve nickname → player dict with parsed lifetime stats."""
    pl = await _api().get_player_by_nickname(nickname.strip())
    pid = pl.get("player_id")
    if not pid:
        raise FaceitAPIError("No player_id in response")
    p, st = await asyncio.gather(
        _api().get_player_by_id(pid),
        _api().get_player_stats_lifetime(pid),
    )
    g = extract_cs2_game(p) or {}
    life = lifetime_map_from_stats_response(st if isinstance(st, dict) else None)
    parsed = parse_lifetime_stats(life)
    return {
        "player_id": pid,
        "nickname": p.get("nickname") or nickname,
        "elo": int(g.get("faceit_elo") or 0),
        "level": int(g.get("skill_level") or 0),
        "region": str(g.get("region") or "—"),
        "country": (p.get("country") or "").upper() or "—",
        "kd": parsed["kd"],
        "hs_pct": parsed["hs_pct"],
        "win_rate_pct": parsed["win_rate_pct"],
        "matches": parsed["matches"],
        "wins": parsed["wins"],
        "losses": parsed["losses"],
        "longest_win_streak": parsed["longest_win_streak"],
        "avg_kills": parsed["avg_kills"],
        "kr": parsed["kr"],
        "mvps": parsed["mvps"],
        "faceit_url": str(p.get("faceit_url") or ""),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_player_stats(nickname: str) -> str:
    """Return ELO, skill level, region, and lifetime CS2 stats for a FACEIT player.

    Args:
        nickname: FACEIT nickname (case-insensitive).
    """
    try:
        b = await _bundle_for_nickname(nickname)
    except FaceitNotFoundError:
        return json.dumps({"error": f"Player '{nickname}' not found on FACEIT."})
    except FaceitAPIError as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps(
        {
            "nickname": b["nickname"],
            "elo": b["elo"],
            "level": b["level"],
            "region": b["region"],
            "country": b["country"],
            "faceit_url": b["faceit_url"],
            "stats": {
                "kd": _fmt_opt(b["kd"], ".2f"),
                "hs_pct": _fmt_opt(b["hs_pct"], ".1f") + "%" if b["hs_pct"] is not None else "N/A",
                "win_rate_pct": _fmt_opt(b["win_rate_pct"], ".1f") + "%" if b["win_rate_pct"] is not None else "N/A",
                "matches": int(b["matches"]) if b["matches"] is not None else "N/A",
                "wins": int(b["wins"]) if b["wins"] is not None else "N/A",
                "losses": int(b["losses"]) if b["losses"] is not None else "N/A",
                "longest_win_streak": int(b["longest_win_streak"]) if b["longest_win_streak"] is not None else "N/A",
                "avg_kills_per_match": _fmt_opt(b["avg_kills"], ".2f"),
                "kr": _fmt_opt(b["kr"], ".2f"),
                "mvps": int(b["mvps"]) if b["mvps"] is not None else "N/A",
            },
        },
        indent=2,
    )


@mcp.tool()
async def get_match_history(nickname: str, limit: int = 10) -> str:
    """Return the most recent CS2 matches for a FACEIT player.

    Args:
        nickname: FACEIT nickname.
        limit: Number of matches to return (1–20, default 10).
    """
    limit = max(1, min(20, limit))
    try:
        pl = await _api().get_player_by_nickname(nickname.strip())
        pid = pl.get("player_id")
        if not pid:
            raise FaceitAPIError("No player_id in response")
        raw = await _api().get_player_match_stats(pid, limit=limit, offset=0)
    except FaceitNotFoundError:
        return json.dumps({"error": f"Player '{nickname}' not found on FACEIT."})
    except FaceitAPIError as exc:
        return json.dumps({"error": str(exc)})

    items = (raw or {}).get("items") or []
    matches: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        stats = it.get("stats")
        if not isinstance(stats, dict):
            continue
        row = parse_match_stats_row(stats)
        matches.append(
            {
                "match_id": row.get("match_id"),
                "map": row.get("map") or "—",
                "result": "Win" if row["won"] is True else ("Loss" if row["won"] is False else "Unknown"),
                "kd": _fmt_opt(row.get("kd"), ".2f"),
                "kills": int(row["kills"]) if row.get("kills") is not None else "N/A",
                "deaths": int(row["deaths"]) if row.get("deaths") is not None else "N/A",
                "hs_pct": _fmt_opt(row.get("hs_pct"), ".0f") + "%" if row.get("hs_pct") is not None else "N/A",
                "mvps": int(row["mvps"]) if row.get("mvps") is not None else "N/A",
                "kr": _fmt_opt(row.get("kr"), ".2f"),
                "finished_at": row.get("finished_at"),
            }
        )

    return json.dumps(
        {"nickname": nickname, "matches_returned": len(matches), "matches": matches},
        indent=2,
    )


@mcp.tool()
async def compare_players(nicknames: list[str]) -> str:
    """Compare CS2 FACEIT stats for 2–6 players side by side.

    Args:
        nicknames: List of 2–6 FACEIT nicknames.
    """
    if len(nicknames) < 2:
        return json.dumps({"error": "Provide at least 2 nicknames."})
    if len(nicknames) > 6:
        return json.dumps({"error": "Maximum 6 nicknames supported."})

    results = await asyncio.gather(
        *[_bundle_for_nickname(n) for n in nicknames],
        return_exceptions=True,
    )

    players: list[dict[str, Any]] = []
    errors: list[str] = []
    for nick, res in zip(nicknames, results):
        if isinstance(res, FaceitNotFoundError):
            errors.append(f"{nick}: not found")
        elif isinstance(res, Exception):
            errors.append(f"{nick}: {res}")
        else:
            players.append(res)

    if len(players) < 2:
        return json.dumps({"error": "Could not load enough players.", "details": errors})

    output: list[dict[str, Any]] = []
    for b in players:
        output.append(
            {
                "nickname": b["nickname"],
                "elo": b["elo"],
                "level": b["level"],
                "kd": _fmt_opt(b["kd"], ".2f"),
                "hs_pct": _fmt_opt(b["hs_pct"], ".1f") + "%" if b["hs_pct"] is not None else "N/A",
                "win_rate_pct": _fmt_opt(b["win_rate_pct"], ".1f") + "%" if b["win_rate_pct"] is not None else "N/A",
                "matches": int(b["matches"]) if b["matches"] is not None else "N/A",
                "avg_kills": _fmt_opt(b["avg_kills"], ".2f"),
                "kr": _fmt_opt(b["kr"], ".2f"),
            }
        )

    return json.dumps(
        {"players": output, "skipped": errors if errors else None},
        indent=2,
    )


@mcp.tool()
async def get_leaderboard() -> str:
    """Return all bot-registered users ranked by their current live FACEIT CS2 ELO."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT telegram_id, faceit_nickname, faceit_player_id FROM users ORDER BY faceit_nickname COLLATE NOCASE"
            ) as cur:
                users = [dict(r) for r in await cur.fetchall()]
    except Exception as exc:
        return json.dumps({"error": f"DB error: {exc}"})

    if not users:
        return json.dumps({"leaderboard": [], "note": "No registered users."})

    _SEM = asyncio.Semaphore(8)

    async def _fetch(u: dict) -> dict[str, Any]:
        async with _SEM:
            try:
                p = await _api().get_player_by_id(u["faceit_player_id"])
            except FaceitAPIError:
                return {"nickname": u["faceit_nickname"], "elo": 0, "level": 0}
        g = extract_cs2_game(p) or {}
        return {
            "nickname": str(p.get("nickname") or u["faceit_nickname"]),
            "elo": int(g.get("faceit_elo") or 0),
            "level": int(g.get("skill_level") or 0),
        }

    rows = await asyncio.gather(*[_fetch(u) for u in users], return_exceptions=True)
    valid = [r for r in rows if isinstance(r, dict)]
    valid.sort(key=lambda r: -r["elo"])

    return json.dumps(
        {"registered_users": len(users), "leaderboard": valid},
        indent=2,
    )


@mcp.tool()
async def get_elo_trend(nickname: str) -> str:
    """Return stored ELO snapshots for a bot-registered FACEIT player.

    Snapshots are recorded whenever the player uses /rank or /stats in the Telegram bot.

    Args:
        nickname: FACEIT nickname (must be registered with the bot).
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT telegram_id FROM users WHERE faceit_nickname = ? COLLATE NOCASE LIMIT 1",
                (nickname.strip(),),
            ) as cur:
                row = await cur.fetchone()

            if not row:
                return json.dumps(
                    {
                        "error": f"'{nickname}' is not registered with the bot. "
                        "Only users who linked their FACEIT account have stored ELO history."
                    }
                )

            tid = row["telegram_id"]
            async with db.execute(
                """
                SELECT elo, level, recorded_at FROM elo_snapshots
                WHERE telegram_id = ?
                ORDER BY recorded_at ASC
                """,
                (tid,),
            ) as cur:
                snaps = [dict(r) for r in await cur.fetchall()]
    except Exception as exc:
        return json.dumps({"error": f"DB error: {exc}"})

    if not snaps:
        return json.dumps(
            {
                "nickname": nickname,
                "snapshots": [],
                "note": "No ELO history yet. Snapshots are saved after /rank or /stats.",
            }
        )

    elos = [s["elo"] for s in snaps]
    return json.dumps(
        {
            "nickname": nickname,
            "snapshots_count": len(snaps),
            "elo_min": min(elos),
            "elo_max": max(elos),
            "elo_latest": elos[-1],
            "elo_change_total": elos[-1] - elos[0],
            "snapshots": snaps,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _amain() -> None:
    global _http, _faceit
    if not FACEIT_API_KEY:
        raise SystemExit("FACEIT_API_KEY is not set. Add it to your .env or pass it as an env var.")
    async with aiohttp.ClientSession() as http:
        _http = http
        _faceit = FaceitAPI(http, FACEIT_API_KEY, cache=TTLCache(maxsize=500))
        await mcp.run_async(transport="stdio")


if __name__ == "__main__":
    asyncio.run(_amain())
