"""Async FACEIT Data API v4 client — with TTL cache, HTTP timeout, and retry."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from config import (
    FACEIT_BASE_URL,
    FACEIT_CIRCUIT_FAILURE_THRESHOLD,
    FACEIT_CIRCUIT_OPEN_SEC,
    FACEIT_RETRY_BASE_DELAY_SEC,
    FACEIT_RETRY_EXTRA_ATTEMPTS,
    FACEIT_RETRY_MAX_DELAY_SEC,
    GAME_ID,
    HTTP_TIMEOUT_SEC,
)

if TYPE_CHECKING:
    from cache import TTLCache

logger = logging.getLogger(__name__)

# Cache TTLs (seconds)
_TTL_PLAYER = 60.0          # profile + ELO change after each match
_TTL_NICKNAME = 120.0       # nickname→id rarely changes
_TTL_LIFETIME = 120.0       # aggregate stats change slowly
_TTL_MATCH_STATS = 60.0     # recent match list
_TTL_MATCH_META = 600.0     # finished match metadata never changes

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)


def _backoff_seconds(attempt_index: int) -> float:
    """Exponential delay capped at FACEIT_RETRY_MAX_DELAY_SEC."""
    raw = FACEIT_RETRY_BASE_DELAY_SEC * (2**attempt_index)
    return min(FACEIT_RETRY_MAX_DELAY_SEC, raw)


class FaceitAPIError(Exception):
    """Base error for FACEIT client."""


class FaceitNotFoundError(FaceitAPIError):
    """404 from FACEIT."""


class FaceitUnavailableError(FaceitAPIError):
    """Network / 5xx / 503."""


class FaceitCircuitOpenError(FaceitUnavailableError):
    """Local circuit breaker: skip outbound calls after repeated failures."""


class FaceitRateLimitError(FaceitAPIError):
    """429 Too Many Requests."""


def _pick_lifetime_value(lifetime: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in lifetime:
            return lifetime[k]
    lower = {str(k).strip().lower(): v for k, v in lifetime.items()}
    for k in keys:
        kl = k.strip().lower()
        if kl in lower:
            return lower[kl]
    return None


def _pick_first_key_substring(lifetime: dict[str, Any], needle: str) -> Any:
    """When FACEIT uses varying labels (e.g. CS2 segments), match by substring in the key."""
    n = needle.lower()
    for k, v in sorted(lifetime.items(), key=lambda kv: str(kv[0])):
        if v is None or v == "":
            continue
        if n in str(k).lower():
            return v
    return None


def _pick_mvp_like(lifetime: dict[str, Any]) -> Any:
    for k, v in sorted(lifetime.items(), key=lambda kv: str(kv[0])):
        if v is None or v == "":
            continue
        kl = str(k).lower()
        if "mvp" in kl:
            return v
    return None


def _pick_rounds_like(lifetime: dict[str, Any]) -> Any:
    """Prefer total/played rounds; avoid win-rate / per-round averages."""
    scored: list[tuple[int, Any, str]] = []
    for k, v in lifetime.items():
        if v is None or v == "":
            continue
        kl = str(k).lower()
        if "round" not in kl or "win" in kl:
            continue
        if "per" in kl and "round" in kl:
            continue
        score = 0
        if "total" in kl:
            score += 2
        if "played" in kl or "rounds" == kl.strip().lower():
            score += 2
        if "rounds" in kl:
            score += 1
        scored.append((score, v, kl))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[2]))
    return scored[0][1]


def _pick_kr_like(lifetime: dict[str, Any]) -> Any:
    for needle in (
        "kills per round",
        "average k/r",
        "average kr",
        "k/r ratio",
        "kpr",
    ):
        v = _pick_first_key_substring(lifetime, needle)
        if v is not None:
            return v
    return None


def _segment_sort_key(segment: Any) -> tuple[str, str]:
    """Stable ordering so merging segment stats does not depend on API list order."""
    if not isinstance(segment, dict):
        return ("", "")
    name = str(
        segment.get("label")
        or segment.get("name")
        or segment.get("mode")
        or segment.get("type")
        or ""
    )
    sid = str(segment.get("segment_id") or segment.get("id") or "")
    return (name, sid)


def lifetime_map_from_stats_response(st: dict[str, Any] | None) -> dict[str, Any]:
    """
    Build one label→value map from GET /players/{id}/stats/{game}.

    CS2 responses often put core rows in `lifetime` but extra totals (Kills, Rounds, …)
    only under `segments[].stats` (dict or list of {label, value}). Merge so parsers see all keys.
    """
    if not isinstance(st, dict):
        return {}

    def merge_missing(src: dict[str, Any]) -> None:
        for k, v in src.items():
            if v is None or v == "":
                continue
            ks = str(k).strip()
            cur = merged.get(ks)
            if cur is None or cur == "":
                merged[ks] = v

    merged: dict[str, Any] = {}
    life = st.get("lifetime")
    if isinstance(life, dict):
        merge_missing(life)

    segments = st.get("segments")
    if isinstance(segments, list):
        for seg in sorted(segments, key=_segment_sort_key):
            if not isinstance(seg, dict):
                continue
            raw = seg.get("stats")
            if isinstance(raw, dict):
                merge_missing(raw)
            elif isinstance(raw, list):
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    label = row.get("label") or row.get("name") or row.get("key")
                    if label is None:
                        continue
                    val = row.get("value")
                    if val is None and "count" in row:
                        val = row.get("count")
                    if val is None:
                        continue
                    merge_missing({str(label): val})

    return merged


def _enrich_lifetime_stats(p: dict[str, Any]) -> None:
    """Fill missing W/L, totals, and averages when FACEIT omits keys but enough signal exists."""
    m = p.get("matches")
    mf = float(m) if m is not None else None

    if mf is not None and mf > 0:
        if p.get("losses") is None and p.get("wins") is not None:
            p["losses"] = max(0.0, mf - float(p["wins"]))
        if p.get("wins") is None and p.get("losses") is not None:
            p["wins"] = max(0.0, mf - float(p["losses"]))

    wr = p.get("win_rate_pct")
    if (
        p.get("wins") is None
        and p.get("losses") is None
        and mf is not None
        and mf > 0
        and wr is not None
    ):
        mi = int(round(mf))
        w = int(round(mf * float(wr) / 100.0))
        w = max(0, min(w, mi))
        p["wins"] = float(w)
        p["losses"] = float(mi - w)

    if mf is not None and mf > 0:
        if p.get("kills") is None and p.get("avg_kills") is not None:
            p["kills"] = float(p["avg_kills"]) * mf
        if p.get("deaths") is None and p.get("avg_deaths") is not None:
            p["deaths"] = float(p["avg_deaths"]) * mf

    if p.get("kills") is None and p.get("kd") is not None and p.get("deaths"):
        df = float(p["deaths"])
        if df > 0:
            p["kills"] = float(p["kd"]) * df

    if p.get("deaths") is None and p.get("kd") is not None and p.get("kills"):
        kdf = float(p["kd"])
        if kdf > 0:
            p["deaths"] = float(p["kills"]) / kdf

    if p.get("kr") is None and p.get("kills") is not None and p.get("rounds"):
        rf = float(p["rounds"])
        if rf > 0:
            p["kr"] = float(p["kills"]) / rf

    if mf is not None and mf > 0:
        if p.get("avg_kills") is None and p.get("kills") is not None:
            p["avg_kills"] = float(p["kills"]) / mf
        if p.get("avg_deaths") is None and p.get("deaths") is not None:
            p["avg_deaths"] = float(p["deaths"]) / mf

    if p.get("rounds") is None and p.get("kr") is not None and p.get("kills"):
        try:
            kr = float(p["kr"])
            if kr > 0:
                p["rounds"] = float(p["kills"]) / kr
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Last pass: W/L still missing but we have matches + win rate (CS2 often omits explicit W/L).
    _finalize_wl_from_matches_wr(p)
    # K/R after rounds may have been filled from segments or inferred above.
    if p.get("kr") is None and p.get("kills") is not None and p.get("rounds"):
        try:
            rf = float(p["rounds"])
            if rf > 0:
                p["kr"] = float(p["kills"]) / rf
        except (TypeError, ValueError, ZeroDivisionError):
            pass


def _finalize_wl_from_matches_wr(p: dict[str, Any]) -> None:
    wr = p.get("win_rate_pct")
    m = p.get("matches")
    if wr is None or m is None:
        return
    try:
        mf = float(m)
        wrf = float(wr)
    except (TypeError, ValueError):
        return
    if mf <= 0:
        return

    wn = p.get("wins")
    ls = p.get("losses")

    if wn is None and ls is None:
        mi = int(round(mf))
        w = int(round(mf * wrf / 100.0))
        w = max(0, min(w, mi))
        p["wins"] = float(w)
        p["losses"] = float(mi - w)
        return
    if ls is None and wn is not None:
        try:
            p["losses"] = max(0.0, mf - float(wn))
        except (TypeError, ValueError):
            return
    elif wn is None and ls is not None:
        try:
            p["wins"] = max(0.0, mf - float(ls))
        except (TypeError, ValueError):
            return


def parse_lifetime_stats(lifetime: dict[str, Any]) -> dict[str, Any]:
    """Normalize lifetime stats; FACEIT uses human-readable label keys."""
    matches = _pick_lifetime_value(
        lifetime, "Matches", "Total Matches", "Number of Matches", "Games"
    )
    win_rate = _pick_lifetime_value(lifetime, "Win Rate %", "Win Rate", "Win Rate % ")
    kd = _pick_lifetime_value(
        lifetime,
        "Average K/D Ratio",
        "Average K/D",
        "K/D Ratio",
        "Average KDR",
        "KDR",
    )
    hs = _pick_lifetime_value(
        lifetime,
        "Average Headshots %",
        "Headshots %",
        "Average Headshots",
    )
    streak = _pick_lifetime_value(
        lifetime,
        "Longest Win Streak",
        "Longest Win Streak ",
        "Best Win Streak",
    )
    wins = _pick_lifetime_value(
        lifetime,
        "Wins",
        "Total Wins",
        "Games Won",
        "Match Wins",
        "Game Wins",
        "Games Win",
    )
    losses = _pick_lifetime_value(
        lifetime,
        "Losses",
        "Total Losses",
        "Games Lost",
        "Match Losses",
        "Game Losses",
        "Games Loss",
    )
    kills = _pick_lifetime_value(
        lifetime, "Kills", "Total Kills", "Total kills", "Kill Count"
    )
    deaths = _pick_lifetime_value(
        lifetime, "Deaths", "Total Deaths", "Total deaths"
    )
    assists = _pick_lifetime_value(
        lifetime, "Assists", "Total Assists", "Total assists"
    )
    rounds = _pick_lifetime_value(
        lifetime,
        "Rounds",
        "Total Rounds",
        "Rounds Played",
        "Total Rounds Played",
        "Rounds played",
    )
    if rounds is None:
        rounds = _pick_rounds_like(lifetime)
    mvps = _pick_lifetime_value(
        lifetime,
        "MVPs",
        "MVP",
        "Total MVPs",
        "Total MVP",
        "MVP Stars",
        "Most Valuable Player",
    )
    if mvps is None:
        mvps = _pick_mvp_like(lifetime)
    avg_kills = _pick_lifetime_value(
        lifetime,
        "Average Kills",
        "Avg Kills",
        "Kills / Match",
        "Kills per Match",
        "Average Kills per Match",
    )
    avg_deaths = _pick_lifetime_value(
        lifetime,
        "Average Deaths",
        "Avg Deaths",
        "Deaths / Match",
        "Deaths per Match",
        "Average Deaths per Match",
    )
    kr = _pick_lifetime_value(
        lifetime,
        "Average K/R Ratio",
        "K/R Ratio",
        "Average KR",
        "Average K/R",
        "KPR",
        "K/R",
        "Average Kills per Round",
        "Kills per Round",
        "Kills Per Round",
    )
    if kr is None:
        kr = _pick_kr_like(lifetime)
    headshots = _pick_lifetime_value(lifetime, "Headshots", "Total Headshots")
    result = {
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
    _enrich_lifetime_stats(result)
    return result


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


def steam_community_url(player: dict[str, Any]) -> str | None:
    """If the FACEIT player payload includes a SteamID64, return the community profile URL."""

    def _profiles_url(raw: Any) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s.isdigit():
            return None
        # SteamID64 is 17 digits (7656119…); reject short noise.
        if len(s) < 15:
            return None
        return f"https://steamcommunity.com/profiles/{s}"

    for key in ("steam_id_64", "steam64", "steam_id"):
        u = _profiles_url(player.get(key))
        if u:
            return u

    pl = player.get("platforms")
    if isinstance(pl, dict):
        st = pl.get("steam") or pl.get("STEAM")
        if isinstance(st, dict):
            for key in ("id", "player_id", "steam_id", "steam_id_64", "steam64"):
                u = _profiles_url(st.get(key))
                if u:
                    return u
        elif st is not None:
            u = _profiles_url(st)
            if u:
                return u

    g = extract_cs2_game(player) or {}
    for key in ("steam_id_64", "steam_id", "steam64"):
        u = _profiles_url(g.get(key))
        if u:
            return u
    nested = g.get("platforms")
    if isinstance(nested, dict):
        st = nested.get("steam") or nested.get("STEAM")
        if isinstance(st, dict):
            for key in ("id", "player_id", "steam_id", "steam_id_64", "steam64"):
                u = _profiles_url(st.get(key))
                if u:
                    return u

    return None


class FaceitAPI:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        cache: "TTLCache | None" = None,
    ) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._cache = cache
        self._circuit_open_until: float = 0.0
        self._circuit_fail_streak: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _do_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Single HTTP attempt — raises typed errors on failure."""
        async with self._session.request(
            method, url, headers=self._headers, timeout=_HTTP_TIMEOUT, **kwargs
        ) as resp:
            if resp.status == 404:
                raise FaceitNotFoundError("Not found")
            if resp.status == 429:
                raise FaceitRateLimitError("Rate limited")
            if resp.status >= 500 or resp.status == 503:
                raise FaceitUnavailableError(f"Server error {resp.status}")
            if resp.status >= 400:
                text = await resp.text()
                logger.warning("FACEIT error %s: %s", resp.status, text[:200])
                raise FaceitAPIError(f"API error {resp.status}")
            return await resp.json()

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """GET with automatic retry on 429 / 5xx / timeout."""
        url = f"{FACEIT_BASE_URL}{path}"
        last_exc: Exception = FaceitAPIError("unknown")
        max_attempt = FACEIT_RETRY_EXTRA_ATTEMPTS

        if FACEIT_CIRCUIT_FAILURE_THRESHOLD > 0:
            now = time.monotonic()
            if now < self._circuit_open_until:
                raise FaceitCircuitOpenError(
                    "FACEIT circuit open — repeated errors; cooling down."
                )

        for attempt in range(max_attempt + 1):
            try:
                result = await self._do_request(method, url, **kwargs)
                self._circuit_fail_streak = 0
                return result
            except FaceitRateLimitError as exc:
                last_exc = exc
                if attempt < max_attempt:
                    delay = _backoff_seconds(attempt)
                    logger.info(
                        "FACEIT rate-limited; sleep %.1fs (attempt %s/%s)",
                        delay,
                        attempt + 2,
                        max_attempt + 1,
                    )
                    await asyncio.sleep(delay)
            except FaceitUnavailableError as exc:
                last_exc = exc
                if attempt < max_attempt:
                    delay = _backoff_seconds(attempt)
                    logger.info(
                        "FACEIT unavailable (%s); retry in %.1fs…",
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
            except aiohttp.ServerTimeoutError:
                last_exc = FaceitUnavailableError(f"Request timed out after {HTTP_TIMEOUT_SEC}s")
                if attempt < max_attempt:
                    delay = _backoff_seconds(attempt)
                    logger.info("FACEIT request timed out; retry in %.1fs…", delay)
                    await asyncio.sleep(delay)
            except aiohttp.ClientError as exc:
                raise FaceitUnavailableError(str(exc)) from exc
            except (FaceitNotFoundError, FaceitAPIError):
                raise  # never retry client errors

        if FACEIT_CIRCUIT_FAILURE_THRESHOLD > 0 and isinstance(
            last_exc, (FaceitRateLimitError, FaceitUnavailableError)
        ):
            self._circuit_fail_streak += 1
            if self._circuit_fail_streak >= FACEIT_CIRCUIT_FAILURE_THRESHOLD:
                self._circuit_open_until = time.monotonic() + FACEIT_CIRCUIT_OPEN_SEC
                self._circuit_fail_streak = 0
                logger.warning(
                    "FACEIT circuit breaker open for %.0fs after repeated failures",
                    FACEIT_CIRCUIT_OPEN_SEC,
                )

        raise last_exc

    async def _cached_get(self, key: str, ttl: float, path: str, **kwargs: Any) -> Any:
        """GET with optional cache lookup/store around the real request."""
        if self._cache is not None:
            hit = self._cache.get(key, ttl)
            if hit is not None:
                logger.debug("cache hit: %s", key)
                return hit
        result = await self._request_json("GET", path, **kwargs)
        if self._cache is not None:
            self._cache.set(key, result)
        return result

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_player_by_nickname(self, nickname: str) -> dict[str, Any]:
        key = f"nick:{nickname.lower()}"
        params = {"nickname": nickname, "game": GAME_ID}
        return await self._cached_get(key, _TTL_NICKNAME, "/players", params=params)

    async def get_player_by_id(self, player_id: str) -> dict[str, Any]:
        key = f"player:{player_id}"
        return await self._cached_get(key, _TTL_PLAYER, f"/players/{player_id}")

    async def get_player_stats_lifetime(self, player_id: str) -> dict[str, Any]:
        key = f"lifetime:{player_id}"
        return await self._cached_get(
            key, _TTL_LIFETIME, f"/players/{player_id}/stats/{GAME_ID}"
        )

    async def get_player_match_stats(
        self, player_id: str, limit: int = 10, offset: int = 0
    ) -> dict[str, Any]:
        """Per-match rows with stats (K/D, map, etc.) — games/{game}/stats."""
        key = f"match_stats:{player_id}:{limit}:{offset}"
        path = f"/players/{player_id}/games/{GAME_ID}/stats"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        return await self._cached_get(key, _TTL_MATCH_STATS, path, params=params)

    async def get_dashboard_bundle(
        self, player_id: str, recent_limit: int
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """
        Player profile + lifetime stats + recent match rows as **one** snapshot.

        The dashboard used to call three separately cached endpoints (different TTLs),
        which mixed fresh ELO with stale K/D or vice versa. One cache entry keeps
        combat averages, totals, and recent form aligned until the next refresh.
        """
        key = f"dash:{player_id}:{recent_limit}"
        ttl = min(_TTL_PLAYER, _TTL_MATCH_STATS, _TTL_LIFETIME)
        if self._cache is not None:
            hit = self._cache.get(key, ttl)
            if hit is not None:
                return hit["player"], hit["lifetime"], hit["recent"]
        path_p = f"/players/{player_id}"
        path_st = f"/players/{player_id}/stats/{GAME_ID}"
        path_m = f"/players/{player_id}/games/{GAME_ID}/stats"
        params_m: dict[str, Any] = {"limit": recent_limit, "offset": 0}
        p, st, recent_raw = await asyncio.gather(
            self._request_json("GET", path_p),
            self._request_json("GET", path_st),
            self._request_json("GET", path_m, params=params_m),
        )
        if self._cache is not None:
            bundle = {"player": p, "lifetime": st, "recent": recent_raw}
            self._cache.set(key, bundle)
            self._cache.set(f"player:{player_id}", p)
            self._cache.set(f"lifetime:{player_id}", st)
            self._cache.set(f"match_stats:{player_id}:{recent_limit}:0", recent_raw)
        return p, st, recent_raw

    async def get_player_history(self, player_id: str, limit: int = 10) -> dict[str, Any]:
        key = f"history:{player_id}:{limit}"
        params = {"game": GAME_ID, "limit": limit, "offset": 0}
        return await self._cached_get(
            key, _TTL_MATCH_STATS, f"/players/{player_id}/history", params=params
        )

    async def get_match_stats(self, match_id: str) -> dict[str, Any]:
        key = f"match_stats_detail:{match_id}"
        return await self._cached_get(key, _TTL_MATCH_META, f"/matches/{match_id}/stats")

    async def get_match(self, match_id: str) -> dict[str, Any]:
        """Match metadata: competition, status, teams, results, region, urls."""
        key = f"match_meta:{match_id}"
        return await self._cached_get(key, _TTL_MATCH_META, f"/matches/{match_id}")


def faceit_match_url(match_id: str) -> str:
    """Canonical CS2 matchroom on the FACEIT website (fallback when API omits a URL).

    Public pages use ``/en/cs2/room/{id}``; ``/cs2/match/`` returns 404.
    """
    mid = (match_id or "").strip()
    if not mid:
        return ""
    return f"https://www.faceit.com/en/cs2/room/{mid}"


def _normalize_faceit_cs2_room_url(url: str) -> str:
    """Rewrite legacy ``/cs2/match/`` links from the API to working ``/cs2/room/`` paths."""
    u = url.strip()
    for old, new in (
        ("/en/cs2/match/", "/en/cs2/room/"),
        ("/cs2/match/", "/cs2/room/"),
    ):
        if old in u:
            return u.replace(old, new, 1)
    return u


def resolve_match_faceit_url(meta: dict[str, Any] | None, match_id: str) -> str:
    """Prefer URL from match payload; otherwise build from *match_id*."""
    if isinstance(meta, dict):
        for key in ("faceit_url", "faceitUrl", "url"):
            v = meta.get(key)
            if isinstance(v, str) and v.strip().lower().startswith("http"):
                return _normalize_faceit_cs2_room_url(v.strip())
    return faceit_match_url(match_id)


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
    hs = _first_present(
        stats,
        "Average Headshots %",
        "Headshots %",
        "Average Headshots",
    )
    mvps = _first_present(
        stats,
        "MVPs",
        "MVP",
        "Total MVPs",
        "Total MVP",
        "MVP Stars",
    )
    kr = _first_present(
        stats,
        "Average K/R Ratio",
        "K/R Ratio",
        "Average K/R",
        "K/R",
        "Average Kills per Round",
        "Kills per Round",
        "Kills Per Round",
    )
    rounds = _first_present(
        stats,
        "Rounds",
        "Rounds Played",
        "Total Rounds",
        "Total Rounds Played",
    )
    return {
        "match_id": str(match_id) if match_id else None,
        "won": won,
        "kills": _to_float(kills),
        "deaths": _to_float(deaths),
        "kd": _to_float(kd),
        "map": str(map_name) if map_name else "—",
        "finished_at": finished,
        "hs_pct": _to_float(hs),
        "mvps": _to_float(mvps),
        "kr": _to_float(kr),
        "rounds": _to_float(rounds),
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


def current_win_streak(items: list[dict[str, Any]]) -> tuple[bool, int] | None:
    """Return (is_win, streak_length) for the current consecutive run, or None."""
    streak_type: bool | None = None
    count = 0
    for it in items:
        if not isinstance(it, dict):
            break
        stats = it.get("stats")
        if not isinstance(stats, dict):
            break
        row = parse_match_stats_row(stats)
        won = row.get("won")
        if won is None:
            break
        if streak_type is None:
            streak_type = won
            count = 1
        elif won == streak_type:
            count += 1
        else:
            break
    if streak_type is None:
        return None
    return streak_type, count


def aggregate_match_scoreboard(match_stats: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sum player stats across all rounds in /matches/{id}/stats.
    Returns flat rows: team_id, nickname, player_id, kills, deaths, assists, hs_pct.
    """
    rounds = match_stats.get("rounds") or []
    if not rounds and match_stats.get("teams"):
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
        hs_pct = row["hs_sum"] / row["hs_count"] if row["hs_count"] else None
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
