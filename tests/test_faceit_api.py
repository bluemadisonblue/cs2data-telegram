"""Tests for faceit_api.py: parsers, aggregators, cache integration."""

import sys
import os
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cache import TTLCache
from faceit_api import (
    _first_present,
    _infer_win,
    _to_float,
    aggregate_match_scoreboard,
    current_win_streak,
    faceit_match_url,
    group_rows_by_team,
    lifetime_map_from_stats_response,
    parse_lifetime_stats,
    parse_match_stats_row,
    resolve_match_faceit_url,
    steam_community_url,
)


# ---------------------------------------------------------------------------
# _to_float
# ---------------------------------------------------------------------------

class TestToFloat:
    def test_int(self):
        assert _to_float(5) == 5.0

    def test_float(self):
        assert _to_float(1.23) == pytest.approx(1.23)

    def test_string_number(self):
        assert _to_float("1.23") == pytest.approx(1.23)

    def test_string_with_percent(self):
        assert _to_float("52.3%") == pytest.approx(52.3)

    def test_string_int(self):
        assert _to_float("42") == 42.0

    def test_none(self):
        assert _to_float(None) is None

    def test_invalid_string(self):
        assert _to_float("abc") is None

    def test_empty_string(self):
        assert _to_float("") is None

    def test_zero(self):
        assert _to_float(0) == 0.0

    def test_negative(self):
        assert _to_float(-1.5) == pytest.approx(-1.5)

    def test_string_with_spaces(self):
        assert _to_float("  3.14  ") == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# _infer_win
# ---------------------------------------------------------------------------

class TestInferWin:
    @pytest.mark.parametrize("val", ["1", "win", "won", "true", "w", "WIN", "True", "W"])
    def test_truthy_values(self, val):
        assert _infer_win(val) is True

    @pytest.mark.parametrize("val", ["0", "loss", "lose", "false", "l", "LOSS", "False", "L"])
    def test_falsy_values(self, val):
        assert _infer_win(val) is False

    def test_none(self):
        assert _infer_win(None) is None

    def test_bool_true(self):
        assert _infer_win(True) is True

    def test_bool_false(self):
        assert _infer_win(False) is False

    def test_unknown_string(self):
        assert _infer_win("maybe") is None

    def test_empty_string(self):
        assert _infer_win("") is None

    def test_numeric_2(self):
        assert _infer_win(2) is None  # not in the known set


# ---------------------------------------------------------------------------
# _first_present
# ---------------------------------------------------------------------------

class TestFirstPresent:
    def test_first_key_found(self):
        d = {"Kills": 10, "Total Kills": 20}
        assert _first_present(d, "Kills", "Total Kills") == 10

    def test_second_key_fallback(self):
        d = {"Total Kills": 20}
        assert _first_present(d, "Kills", "Total Kills") == 20

    def test_no_key_returns_none(self):
        d = {"Something": 1}
        assert _first_present(d, "Kills", "Total Kills") is None

    def test_empty_dict(self):
        assert _first_present({}, "Kills") is None

    def test_no_keys_to_search(self):
        assert _first_present({"Kills": 5}) is None

    def test_value_zero_returned(self):
        d = {"Deaths": 0}
        assert _first_present(d, "Deaths") == 0

    def test_value_none_returned(self):
        d = {"Result": None}
        assert _first_present(d, "Result") is None


# ---------------------------------------------------------------------------
# parse_lifetime_stats
# ---------------------------------------------------------------------------

class TestParseLifetimeStats:
    def test_empty_dict_all_none(self):
        result = parse_lifetime_stats({})
        assert result["matches"] is None
        assert result["kd"] is None
        assert result["win_rate_pct"] is None

    def test_standard_keys(self):
        data = {
            "Matches": "500",
            "Win Rate %": "52.3",
            "Average K/D Ratio": "1.25",
            "Average Headshots %": "18.5",
            "Wins": "260",
            "Losses": "240",
            "Longest Win Streak": "7",
        }
        result = parse_lifetime_stats(data)
        assert result["matches"] == pytest.approx(500.0)
        assert result["win_rate_pct"] == pytest.approx(52.3)
        assert result["kd"] == pytest.approx(1.25)
        assert result["hs_pct"] == pytest.approx(18.5)
        assert result["wins"] == pytest.approx(260.0)
        assert result["losses"] == pytest.approx(240.0)
        assert result["longest_win_streak"] == pytest.approx(7.0)

    def test_alternate_keys(self):
        data = {
            "Total Matches": "100",
            "Win Rate": "48.0",
            "K/D Ratio": "0.98",
            "Headshots %": "15.0",
        }
        result = parse_lifetime_stats(data)
        assert result["matches"] == pytest.approx(100.0)
        assert result["win_rate_pct"] == pytest.approx(48.0)
        assert result["kd"] == pytest.approx(0.98)
        assert result["hs_pct"] == pytest.approx(15.0)

    def test_percent_string_parsed(self):
        result = parse_lifetime_stats({"Win Rate %": "55.5%"})
        assert result["win_rate_pct"] == pytest.approx(55.5)

    def test_integer_values(self):
        result = parse_lifetime_stats({"Matches": 300, "Wins": 150})
        assert result["matches"] == 300.0
        assert result["wins"] == 150.0
        assert result["losses"] == 150.0  # enriched from matches − wins

    def test_enrich_wins_losses_from_win_rate_only(self):
        result = parse_lifetime_stats({"Matches": "100", "Win Rate %": "49"})
        assert result["wins"] == 49.0
        assert result["losses"] == 51.0

    def test_case_insensitive_keys(self):
        result = parse_lifetime_stats({"matches": "10", "wins": "6", "losses": "4"})
        assert result["matches"] == 10.0
        assert result["wins"] == 6.0


class TestLifetimeMapFromStatsResponse:
    def test_segment_merge_order_stable(self):
        """Reordering segments should not change merged values (same keys win by merge rules)."""
        st_a = {
            "lifetime": {},
            "segments": [
                {"label": "z", "stats": {"Average K/D Ratio": "1.0"}},
                {"label": "a", "stats": {"Average K/D Ratio": "9.0"}},
            ],
        }
        st_b = {
            "lifetime": {},
            "segments": list(reversed(st_a["segments"])),
        }
        assert lifetime_map_from_stats_response(st_a) == lifetime_map_from_stats_response(st_b)

    def test_merges_segment_stats_dict(self):
        st = {
            "lifetime": {"Matches": "10", "Win Rate %": "50"},
            "segments": [{"stats": {"Kills": "200", "Deaths": "180", "Rounds": "500"}}],
        }
        m = lifetime_map_from_stats_response(st)
        assert m["Matches"] == "10"
        assert m["Kills"] == "200"
        parsed = parse_lifetime_stats(m)
        assert parsed["kills"] == 200.0
        assert parsed["deaths"] == 180.0
        assert parsed["rounds"] == 500.0

    def test_segment_list_label_value(self):
        st = {
            "lifetime": {"Matches": "5"},
            "segments": [
                {
                    "stats": [
                        {"label": "Kills", "value": "50"},
                        {"label": "Deaths", "value": "40"},
                    ]
                }
            ],
        }
        m = lifetime_map_from_stats_response(st)
        parsed = parse_lifetime_stats(m)
        assert parsed["kills"] == 50.0
        assert parsed["deaths"] == 40.0


# ---------------------------------------------------------------------------
# faceit_match_url / resolve_match_faceit_url
# ---------------------------------------------------------------------------

class TestFaceitMatchUrl:
    def test_builds_cs2_room_path(self):
        assert faceit_match_url("1-abc-uuid") == "https://www.faceit.com/en/cs2/room/1-abc-uuid"

    def test_empty_id(self):
        assert faceit_match_url("") == ""
        assert faceit_match_url("   ") == ""

    def test_resolve_prefers_meta(self):
        u = "https://www.faceit.com/en/cs2/room/x"
        assert resolve_match_faceit_url({"faceit_url": u}, "ignored") == u

    def test_resolve_rewrites_legacy_match_path(self):
        u = "https://www.faceit.com/en/cs2/match/1-deadbeef"
        assert resolve_match_faceit_url({"faceit_url": u}, "ignored") == (
            "https://www.faceit.com/en/cs2/room/1-deadbeef"
        )

    def test_resolve_fallback(self):
        mid = "1-test"
        assert resolve_match_faceit_url({}, mid) == faceit_match_url(mid)


# ---------------------------------------------------------------------------
# parse_match_stats_row
# ---------------------------------------------------------------------------

class TestParseMatchStatsRow:
    def test_basic_win(self):
        stats = {"Kills": "20", "Deaths": "15", "Result": "1", "Map": "de_inferno"}
        row = parse_match_stats_row(stats)
        assert row["kills"] == pytest.approx(20.0)
        assert row["deaths"] == pytest.approx(15.0)
        assert row["won"] is True
        assert row["map"] == "de_inferno"

    def test_loss(self):
        stats = {"Result": "0"}
        row = parse_match_stats_row(stats)
        assert row["won"] is False

    def test_kd_computed_when_missing(self):
        stats = {"Kills": "10", "Deaths": "5", "Result": "1"}
        row = parse_match_stats_row(stats)
        assert row["kd"] == pytest.approx(2.0)

    def test_kd_not_computed_on_zero_deaths(self):
        stats = {"Kills": "10", "Deaths": "0"}
        row = parse_match_stats_row(stats)
        # deaths=0 → kd stays None (would be division by zero)
        assert row["kd"] is None

    def test_empty_stats(self):
        row = parse_match_stats_row({})
        assert row["won"] is None
        assert row["kills"] is None
        assert row["map"] == "—"

    def test_match_id_extracted(self):
        stats = {"Match Id": "abc-123"}
        row = parse_match_stats_row(stats)
        assert row["match_id"] == "abc-123"


# ---------------------------------------------------------------------------
# aggregate_match_scoreboard
# ---------------------------------------------------------------------------

class TestAggregateMatchScoreboard:
    def _make_round(self, teams_data: list[dict]) -> dict:
        return {"rounds": [{"teams": teams_data}]}

    def test_empty_input(self):
        assert aggregate_match_scoreboard({}) == []

    def test_empty_rounds(self):
        assert aggregate_match_scoreboard({"rounds": []}) == []

    def test_single_round_two_teams(self):
        data = {
            "rounds": [
                {
                    "teams": [
                        {
                            "team_id": "team_a",
                            "players": [
                                {
                                    "player_id": "p1",
                                    "nickname": "Alice",
                                    "player_stats": {"Kills": "20", "Deaths": "10", "Assists": "5"},
                                }
                            ],
                        },
                        {
                            "team_id": "team_b",
                            "players": [
                                {
                                    "player_id": "p2",
                                    "nickname": "Bob",
                                    "player_stats": {"Kills": "12", "Deaths": "18", "Assists": "3"},
                                }
                            ],
                        },
                    ]
                }
            ]
        }
        rows = aggregate_match_scoreboard(data)
        assert len(rows) == 2
        alice = next(r for r in rows if r["nickname"] == "Alice")
        bob = next(r for r in rows if r["nickname"] == "Bob")
        assert alice["kills"] == 20.0
        assert alice["kd"] == pytest.approx(2.0)
        assert bob["kills"] == 12.0

    def test_multi_round_kills_summed(self):
        """Kills should be summed across rounds."""
        round_team = {
            "team_id": "team_a",
            "players": [
                {
                    "player_id": "p1",
                    "nickname": "Alice",
                    "player_stats": {"Kills": "10", "Deaths": "5", "Assists": "2"},
                }
            ],
        }
        data = {"rounds": [{"teams": [round_team]}, {"teams": [round_team]}]}
        rows = aggregate_match_scoreboard(data)
        alice = rows[0]
        assert alice["kills"] == 20.0  # 10 + 10
        assert alice["deaths"] == 10.0  # 5 + 5

    def test_hs_pct_averaged(self):
        round_team = {
            "team_id": "team_a",
            "players": [
                {
                    "player_id": "p1",
                    "nickname": "Alice",
                    "player_stats": {"Kills": "10", "Deaths": "5", "Headshots %": "20"},
                }
            ],
        }
        round_team2 = {
            "team_id": "team_a",
            "players": [
                {
                    "player_id": "p1",
                    "nickname": "Alice",
                    "player_stats": {"Kills": "10", "Deaths": "5", "Headshots %": "40"},
                }
            ],
        }
        data = {"rounds": [{"teams": [round_team]}, {"teams": [round_team2]}]}
        rows = aggregate_match_scoreboard(data)
        assert rows[0]["hs_pct"] == pytest.approx(30.0)  # avg of 20 and 40

    def test_missing_team_id_generates_synthetic(self):
        data = {
            "rounds": [
                {
                    "teams": [
                        {
                            "players": [
                                {
                                    "player_id": "p1",
                                    "nickname": "Alice",
                                    "player_stats": {"Kills": "5", "Deaths": "5"},
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        rows = aggregate_match_scoreboard(data)
        assert len(rows) == 1
        assert rows[0]["nickname"] == "Alice"
        # team_id should be a synthetic non-empty string
        assert rows[0]["team_id"] != ""

    def test_sorted_by_team_then_kd(self):
        data = {
            "rounds": [
                {
                    "teams": [
                        {
                            "team_id": "team_a",
                            "players": [
                                {"player_id": "p1", "nickname": "Low", "player_stats": {"Kills": "5", "Deaths": "10"}},
                                {"player_id": "p2", "nickname": "High", "player_stats": {"Kills": "20", "Deaths": "5"}},
                            ],
                        }
                    ]
                }
            ]
        }
        rows = aggregate_match_scoreboard(data)
        assert rows[0]["nickname"] == "High"
        assert rows[1]["nickname"] == "Low"


# ---------------------------------------------------------------------------
# group_rows_by_team
# ---------------------------------------------------------------------------

class TestGroupRowsByTeam:
    def test_empty(self):
        left, right = group_rows_by_team([])
        assert left == []
        assert right == []

    def test_two_teams(self):
        rows = [
            {"team_id": "a", "nickname": "Alice", "kd": 2.0},
            {"team_id": "b", "nickname": "Bob", "kd": 1.0},
        ]
        left, right = group_rows_by_team(rows)
        assert len(left) == 1
        assert len(right) == 1
        assert left[0]["nickname"] == "Alice"
        assert right[0]["nickname"] == "Bob"

    def test_single_team(self):
        rows = [
            {"team_id": "a", "nickname": "Alice", "kd": 2.0},
            {"team_id": "a", "nickname": "Bob", "kd": 1.0},
        ]
        left, right = group_rows_by_team(rows)
        assert len(left) == 2
        assert right == []


# ---------------------------------------------------------------------------
# current_win_streak
# ---------------------------------------------------------------------------

class TestCurrentWinStreak:
    def _make_item(self, result: str) -> dict:
        return {"stats": {"Result": result}}

    def test_empty_returns_none(self):
        assert current_win_streak([]) is None

    def test_single_win(self):
        result = current_win_streak([self._make_item("1")])
        assert result == (True, 1)

    def test_single_loss(self):
        result = current_win_streak([self._make_item("0")])
        assert result == (False, 1)

    def test_three_wins(self):
        items = [self._make_item("1")] * 3
        result = current_win_streak(items)
        assert result == (True, 3)

    def test_streak_breaks_on_opposite(self):
        items = [self._make_item("1"), self._make_item("1"), self._make_item("0")]
        result = current_win_streak(items)
        assert result == (True, 2)

    def test_loss_streak(self):
        items = [self._make_item("0")] * 4
        result = current_win_streak(items)
        assert result == (False, 4)

    def test_unknown_breaks_streak(self):
        items = [self._make_item("1"), {"stats": {"Result": "?"}}]
        result = current_win_streak(items)
        assert result == (True, 1)

    def test_non_dict_item_breaks_streak(self):
        items = [self._make_item("1"), "not_a_dict"]
        result = current_win_streak(items)
        assert result == (True, 1)


# ---------------------------------------------------------------------------
# TTLCache
# ---------------------------------------------------------------------------

class TestTTLCache:
    def test_set_and_get(self):
        cache = TTLCache()
        cache.set("key", {"data": 123})
        result = cache.get("key", ttl=60.0)
        assert result == {"data": 123}

    def test_miss_returns_none(self):
        cache = TTLCache()
        assert cache.get("missing", ttl=60.0) is None

    def test_expired_returns_none(self):
        cache = TTLCache()
        cache.set("key", "value")
        # Patch the timestamp to be in the past
        cache._store["key"] = (time.monotonic() - 10.0, "value")
        assert cache.get("key", ttl=5.0) is None

    def test_not_expired_returns_value(self):
        cache = TTLCache()
        cache.set("key", "value")
        assert cache.get("key", ttl=60.0) == "value"

    def test_invalidate(self):
        cache = TTLCache()
        cache.set("key", "value")
        cache.invalidate("key")
        assert cache.get("key", ttl=60.0) is None

    def test_invalidate_missing_key_noop(self):
        cache = TTLCache()
        cache.invalidate("nonexistent")  # should not raise

    def test_clear(self):
        cache = TTLCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert len(cache) == 0

    def test_len(self):
        cache = TTLCache()
        assert len(cache) == 0
        cache.set("a", 1)
        assert len(cache) == 1
        cache.set("b", 2)
        assert len(cache) == 2

    def test_overwrite(self):
        cache = TTLCache()
        cache.set("key", "old")
        cache.set("key", "new")
        assert cache.get("key", ttl=60.0) == "new"

    def test_expired_entry_removed_on_get(self):
        cache = TTLCache()
        cache.set("key", "value")
        cache._store["key"] = (time.monotonic() - 10.0, "value")
        cache.get("key", ttl=5.0)  # triggers deletion
        assert "key" not in cache._store


# ---------------------------------------------------------------------------
# steam_community_url
# ---------------------------------------------------------------------------


class TestSteamCommunityUrl:
    _sid = "76561198000000000"

    def test_root_steam_id_64(self):
        assert steam_community_url({"steam_id_64": self._sid}) == (
            f"https://steamcommunity.com/profiles/{self._sid}"
        )

    def test_platforms_steam_dict(self):
        p = {"platforms": {"steam": {"id": self._sid}}}
        assert steam_community_url(p) == f"https://steamcommunity.com/profiles/{self._sid}"

    def test_too_short_rejected(self):
        assert steam_community_url({"steam_id_64": "12345"}) is None

    def test_missing_returns_none(self):
        assert steam_community_url({"nickname": "x"}) is None
