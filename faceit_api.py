"""Async FACEIT Data API v4 client (aiohttp)."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from config import FACEIT_BASE_URL, GAME_ID

logger = logging.getLogger(__name__)


class FaceitAPIError(Exception):
    """Base error for FACEIT client."""


class FaceitNotFoundError(FaceitAPIError):
    """404 from FACEIT."""


class FaceitUnavailableError(FaceitAPIError):
    """Network / 5xx / 503."""


class FaceitRateLimitError(FaceitAPIError):
    """429 Too Many Requests."""


def _pick_lifetime_value(lifetime: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in lifetime:
            return lifetime[k]
    return None


def parse_lifetime_stats(lifetime: dict[str, Any]) -> dict[str, Any]:
    """Normalize lifetime stats; FACEIT uses human-readable label keys."""
    matches = _pick_lifetime_value(lifetime, "Matches", "Total Matches")
    win_rate = _pick_lifetime_value(lifetime, "Win Rate %", "Win Rate", "Win Rate % ")
    kd = _pick_lifetime_value(
        lifetime,
        "Average K/D Ratio",
        "Average K/D",
        "K/D Ratio",
        "Average KDR",
    )
    hs = _pick_lifetime_value(
        lifetime,
        "Average Headshots %",
        "Headshots %",
    )
    streak = _pick_lifetime_value(lifetime, "Longest Win Streak", "Longest Win Streak ")
    wins = _pick_lifetime_value(lifetime, "Wins", "Total Wins")
    losses = _pick_lifetime_value(lifetime, "Losses", "Total Losses")
    kills = _pick_lifetime_value(lifetime, "Kills", "Total Kills")
    deaths = _pick_lifetime_value(lifetime, "Deaths", "Total Deaths")
    assists = _pick_lifetime_value(lifetime, "Assists", "Total Assists")
    rounds = _pick_lifetime_value(lifetime, "Rounds", "Total Rounds")
    mvps = _pick_lifetime_value(lifetime, "MVPs", "MVP")
    avg_kills = _pick_lifetime_value(
        lifetime, "Average Kills", "Avg Kills", "Kills / Match"
    )
    avg_deaths = _pick_lifetime_value(
        lifetime, "Average Deaths", "Avg Deaths", "Deaths / Match"
    )
    kr = _pick_lifetime_value(
        lifetime,
        "Average K/R Ratio",
        "K/R Ratio",
        "Average KR",
    )
    headshots = _pick_lifetime_value(lifetime, "Headshots", "Total Headshots")
    return {
        "matches": _to_float(matches),
        "win_rate_pct": _to_float(win_rate),
        "kd": _to_float(kd),
        "hs_pct": _to_float(hs),
        "longest_win_streak": _to_float(streak),
        "wins": _to_float(wins),
        "losses": _to_float(losses),
        "kills": _to_float(kills),
        "deaths": _to_float(deaths),
        "assists": _to_float(assists),
        "rounds": _to_float(rounds),
        "mvps": _to_float(mvps),
        "avg_kills": _to_float(avg_kills),
        "avg_deaths": _to_float(avg_deaths),
        "kr": _to_float(kr),
        "headshots": _to_float(headshots),
    }


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).replace("%", "").strip()
        return float(s)
    except (TypeError, ValueError):
        return None


def extract_cs2_game(player: dict[str, Any]) -> dict[str, Any] | None:
    games = player.get("games") or {}
    return games.get(GAME_ID) or games.get("cs2")


class FaceitAPI:
    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{FACEIT_BASE_URL}{path}"
        try:
            async with self._session.request(
                method, url, headers=self._headers, **kwargs
            ) as resp:
                if resp.status == 404:
                    raise FaceitNotFoundError("Not found")
                if resp.status == 429:
                    raise FaceitRateLimitError("Rate limited")
                if resp.status >= 500 or resp.status == 503:
                    raise FaceitUnavailableError("Server error")
                if resp.status >= 400:
                    text = await resp.text()
                    logger.warning("FACEIT error %s: %s", resp.status, text[:200])
                    raise FaceitAPIError(f"API error {resp.status}")
                return await resp.json()
        except aiohttp.ClientError as e:
            raise FaceitUnavailableError(str(e)) from e

    async def get_player_by_nickname(self, nickname: str) -> dict[str, Any]:
        params = {"nickname": nickname, "game": GAME_ID}
        return await self._request_json("GET", "/players", params=params)

    async def get_player_by_id(self, player_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"/players/{player_id}")

    async def get_player_stats_lifetime(self, player_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"/players/{player_id}/stats/{GAME_ID}")

    async def get_player_match_stats(
        self, player_id: str, limit: int = 10, offset: int = 0
    ) -> dict[str, Any]:
        """Per-match rows with stats (K/D, map, etc.) — games/{game}/stats."""
        path = f"/players/{player_id}/games/{GAME_ID}/stats"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        return await self._request_json("GET", path, params=params)

    async def get_player_history(self, player_id: str, limit: int = 10) -> dict[str, Any]:
        params = {"game": GAME_ID, "limit": limit, "offset": 0}
        return await self._request_json(
            "GET", f"/players/{player_id}/history", params=params
        )

    async def get_match_stats(self, match_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"/matches/{match_id}/stats")

    async def get_match(self, match_id: str) -> dict[str, Any]:
        """Match metadata: competition, status, teams, results, region, urls."""
        return await self._request_json("GET", f"/matches/{match_id}")


def parse_match_stats_row(stats: dict[str, Any]) -> dict[str, Any]:
    """Parse one PlayerStatsForMatch.stats blob."""
    kills = _first_present(stats, "Kills", "Total Kills")
    deaths = _first_present(stats, "Deaths", "Total Deaths")
    kd = _first_present(stats, "K/D Ratio", "KDR", "Average K/D Ratio")
    if kd is None and kills is not None and deaths:
        try:
            kd = float(kills) / float(deaths) if float(deaths) else None
        except (TypeError, ValueError, ZeroDivisionError):
            kd = None
    result = _first_present(stats, "Result", "Game Result")
    won = _infer_win(result)
    map_name = _first_present(stats, "Map", "Map Name")
    finished = _first_present(stats, "Match Finished At", "Finished At")
    match_id = _first_present(stats, "Match Id", "Match ID", "MatchId", "match_id")
    return {
        "match_id": str(match_id) if match_id else None,
        "won": won,
        "kills": _to_float(kills),
        "deaths": _to_float(deaths),
        "kd": _to_float(kd),
        "map": str(map_name) if map_name else "—",
        "finished_at": finished,
    }


def _first_present(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


def _infer_win(result: Any) -> bool | None:
    if result is None:
        return None
    if isinstance(result, bool):
        return result
    s = str(result).strip().lower()
    if s in ("1", "win", "won", "true", "w"):
        return True
    if s in ("0", "loss", "lose", "false", "l"):
        return False
    return None


def aggregate_match_scoreboard(match_stats: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sum player stats across all rounds in /matches/{id}/stats.
    Returns flat rows: team_id, nickname, player_id, kills, deaths, assists, hs_pct.
    """
    rounds = match_stats.get("rounds") or []
    if not rounds and match_stats.get("teams"):
        # Rare shape: teams at root instead of rounds[]
        rounds = [{"teams": match_stats["teams"]}]
    acc: dict[str, dict[str, Any]] = {}

    for rnd in rounds:
        for team in rnd.get("teams") or []:
            team_id = str(team.get("team_id") or team.get("TeamID") or "")
            if not team_id:
                team_id = f"t{id(team)}"
            for pl in team.get("players") or []:
                pid = str(pl.get("player_id") or pl.get("ID") or "")
                nick = str(pl.get("nickname") or "?")
                raw_stats = pl.get("player_stats") or pl.get("PlayerStats") or {}
                if isinstance(raw_stats, str):
                    continue
                kills = _to_float(_first_present(raw_stats, "Kills", "Total Kills")) or 0.0
                deaths = _to_float(_first_present(raw_stats, "Deaths", "Total Deaths")) or 0.0
                assists = _to_float(_first_present(raw_stats, "Assists", "Total Assists")) or 0.0
                hs = _to_float(
                    _first_present(
                        raw_stats,
                        "Headshots %",
                        "Headshots",
                        "Average Headshots %",
                    )
                )
                key = pid or f"{team_id}:{nick}"
                if key not in acc:
                    acc[key] = {
                        "team_id": team_id,
                        "player_id": pid,
                        "nickname": nick,
                        "kills": 0.0,
                        "deaths": 0.0,
                        "assists": 0.0,
                        "hs_sum": 0.0,
                        "hs_count": 0,
                    }
                acc[key]["kills"] += kills
                acc[key]["deaths"] += deaths
                acc[key]["assists"] += assists
                if hs is not None:
                    acc[key]["hs_sum"] += hs
                    acc[key]["hs_count"] += 1

    rows: list[dict[str, Any]] = []
    for row in acc.values():
        d = row["deaths"] or 1.0
        kd = row["kills"] / d
        hs_pct = (
            row["hs_sum"] / row["hs_count"] if row["hs_count"] else None
        )
        rows.append(
            {
                "team_id": row["team_id"],
                "player_id": row["player_id"],
                "nickname": row["nickname"],
                "kills": row["kills"],
                "deaths": row["deaths"],
                "assists": row["assists"],
                "kd": kd,
                "hs_pct": hs_pct,
            }
        )
    # Stable sort: by team then K/D
    rows.sort(key=lambda r: (r["team_id"], -r["kd"]))
    return rows


def group_rows_by_team(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    if not rows:
        return [], []
    team_ids = list(dict.fromkeys(r["team_id"] for r in rows))
    if len(team_ids) < 2:
        return rows, []
    a, b = team_ids[0], team_ids[1]
    left = [r for r in rows if r["team_id"] == a]
    right = [r for r in rows if r["team_id"] == b]
    return left, right
