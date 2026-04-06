"""Load environment variables via python-dotenv."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

BOT_VERSION: str = "1.1.0"

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
FACEIT_API_KEY: str = os.getenv("FACEIT_API_KEY", "")
FACEIT_BASE_URL: str = "https://open.faceit.com/data/v4"
GAME_ID: str = "cs2"
DB_PATH: str = str(Path(__file__).resolve().parent / "bot_data.db")

# Official FACEIT CS2 ELO ranges per level (min ELO inclusive, max inclusive; 10 is open-ended).
LEVEL_ELO_RANGES: list[tuple[int, int, int]] = [
    (1, 100, 500),
    (2, 501, 750),
    (3, 751, 900),
    (4, 901, 1050),
    (5, 1051, 1200),
    (6, 1201, 1350),
    (7, 1351, 1530),
    (8, 1531, 1750),
    (9, 1751, 2000),
    (10, 2001, 999_999),
]


def level_tier_emoji(level: int) -> str:
    """Visual tier emoji buckets (1–10) per product spec."""
    if level <= 2:
        return "🟤"
    if level <= 4:
        return "🟡"
    if level <= 6:
        return "🟠"
    if level <= 8:
        return "🔴"
    if level == 9:
        return "🔵"
    return "🟣"


def elo_progress_in_level(elo: int, level: int) -> tuple[float, int, int | None]:
    """
    Returns (fraction 0-1 within current level band), band_min, next_level_min_or_none).
    Level 10 has no next threshold.
    """
    if level >= 10:
        return 1.0, elo, None
    band = next((b for b in LEVEL_ELO_RANGES if b[0] == level), None)
    if not band:
        return 0.0, elo, None
    _, lo, hi = band
    span = max(hi - lo, 1)
    frac = (elo - lo) / span
    frac = max(0.0, min(1.0, frac))
    next_min = next((b[1] for b in LEVEL_ELO_RANGES if b[0] == level + 1), None)
    return frac, lo, next_min
