"""Tests for config.py: level_tier_emoji and elo_progress_in_level."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import elo_progress_in_level, level_tier_emoji


class TestLevelTierEmoji:
    def test_level_1(self):
        assert level_tier_emoji(1) == "⚪"

    def test_level_2(self):
        assert level_tier_emoji(2) == "🟢"

    def test_level_3(self):
        assert level_tier_emoji(3) == "🟢"

    def test_level_4(self):
        assert level_tier_emoji(4) == "🟡"

    def test_level_5(self):
        assert level_tier_emoji(5) == "🟡"

    def test_level_6(self):
        assert level_tier_emoji(6) == "🟡"

    def test_level_7(self):
        assert level_tier_emoji(7) == "🟡"

    def test_level_8(self):
        assert level_tier_emoji(8) == "🟠"

    def test_level_9(self):
        assert level_tier_emoji(9) == "🟠"

    def test_level_10(self):
        assert level_tier_emoji(10) == "🔴"

    def test_level_11_above_max(self):
        assert level_tier_emoji(11) == "🔴"

    def test_level_0_below_min(self):
        assert level_tier_emoji(0) == "⚪"

    def test_level_negative(self):
        assert level_tier_emoji(-1) == "⚪"


class TestEloProgressInLevel:
    def test_level_10_returns_full(self):
        frac, band_lo, next_min = elo_progress_in_level(2500, 10)
        assert frac == 1.0
        assert next_min is None

    def test_level_10_any_elo_is_full(self):
        frac, _, _ = elo_progress_in_level(2001, 10)
        assert frac == 1.0

    def test_level_1_at_floor(self):
        # ELO at band minimum → fraction near 0
        frac, band_lo, next_min = elo_progress_in_level(100, 1)
        assert frac == pytest.approx(0.0)
        assert next_min == 501  # level 2 floor

    def test_level_1_at_ceiling(self):
        frac, _, _ = elo_progress_in_level(500, 1)
        assert frac == pytest.approx(1.0)

    def test_level_5_midpoint(self):
        # Level 5: 1051–1200 → midpoint ~1125
        frac, _, _ = elo_progress_in_level(1125, 5)
        assert 0.4 < frac < 0.6

    def test_level_9_next_min_is_2001(self):
        _, _, next_min = elo_progress_in_level(1800, 9)
        assert next_min == 2001

    def test_clamp_below_floor(self):
        # ELO below band floor → clamped to 0.0
        frac, _, _ = elo_progress_in_level(50, 1)
        assert frac == 0.0

    def test_clamp_above_ceiling(self):
        # ELO above band ceiling → clamped to 1.0
        frac, _, _ = elo_progress_in_level(9999, 5)
        assert frac == 1.0

    def test_level_above_10_treated_as_max(self):
        # Any level >= 10 is clamped to max (1.0) by the implementation
        frac, _, _ = elo_progress_in_level(1000, 99)
        assert frac == 1.0

    def test_level_0_not_in_bands_returns_zero(self):
        # Level 0 is < 10 but not in ELO_RANGES → fallback 0.0
        frac, _, _ = elo_progress_in_level(1000, 0)
        assert frac == 0.0
