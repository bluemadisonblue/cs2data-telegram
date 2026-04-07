"""Environment and shared constants (loaded from `.env` via python-dotenv)."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

_LOG = logging.getLogger(__name__)
_APP_ROOT = Path(__file__).resolve().parent


def _try_db_file(path: Path, *, warn_on_fail: bool) -> bool:
    """
    True if we can create the DB file's parent dir and write a probe file there.
    mkdir(..., exist_ok=True) alone is not enough: /data may exist but be root-only on App Platform.
    """
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if warn_on_fail:
            _LOG.warning(
                "DB_PATH %s: cannot create parent (%s). Using app directory instead.",
                path,
                exc,
            )
        return False
    probe = parent / f".faceit_bot_write_probe.{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        if warn_on_fail:
            _LOG.warning(
                "DB_PATH %s: directory not writable (%s). Using app directory instead. "
                "On App Platform, remove DB_PATH or use bot_data.db — not /data/... without a volume.",
                path,
                exc,
            )
        return False
    return True


def _compute_db_path() -> str:
    """
    Resolve SQLite path. Relative DB_PATH is under the app directory (not process CWD).
    Tries env DB_PATH first, then bot_data.db next to this package.
    """
    default = _APP_ROOT / "bot_data.db"
    raw = (os.getenv("DB_PATH") or "").strip()
    candidates: list[Path] = []
    if raw:
        p = Path(raw)
        p = (_APP_ROOT / p) if not p.is_absolute() else p
        candidates.append(p.resolve())
    candidates.append(default)

    for i, path in enumerate(candidates):
        warn = bool(raw) and i == 0 and len(candidates) > 1
        if _try_db_file(path, warn_on_fail=warn):
            return str(path)

    raise OSError(
        f"Cannot use any database path (tried {candidates}). Check app directory permissions."
    )


BOT_VERSION: str = "1.4.1"

BOT_TOKEN: str = (os.getenv("BOT_TOKEN") or "").strip()
FACEIT_API_KEY: str = (os.getenv("FACEIT_API_KEY") or "").strip()
FACEIT_BASE_URL: str = "https://open.faceit.com/data/v4"
GAME_ID: str = "cs2"
DB_PATH: str = _compute_db_path()

# Rate limits & UX
COOLDOWN_SEC: float = 10.0
MATCHES_PAGE_SIZE: int = 5
RECENT_FORM_LIMIT: int = 12
LEADERBOARD_MAX_USERS: int = 40
PARTY_MAX_PLAYERS: int = 6
INLINE_STATS_MIN_QUERY_LEN: int = 2

# In-process API cache (LRU)
MAX_CACHE_SIZE: int = 2000

# HTTP / FACEIT retries
HTTP_TIMEOUT_SEC: int = 15
FACEIT_RETRY_EXTRA_ATTEMPTS: int = 1
FACEIT_RETRY_BASE_DELAY_SEC: float = 1.5
FACEIT_RETRY_MAX_DELAY_SEC: float = 10.0

# Background match watch (seconds between polls)
WATCH_POLL_INTERVAL: int = 300

# FACEIT CS2 ELO bands: (level, min_elo inclusive, max_elo inclusive). Level 10 is open-ended.
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
    """Colored circle by FACEIT level (1–10). Level ≤1 / unknown → gray."""
    if level <= 1:
        return "⚪"
    if level <= 3:
        return "🟢"
    if level <= 7:
        return "🟡"
    if level <= 9:
        return "🟠"
    return "🔴"


def elo_progress_in_level(elo: int, level: int) -> tuple[float, int, int | None]:
    """Fraction within current level band, band_min, next_level_min (None at level 10)."""
    if level >= 10:
        return 1.0, elo, None
    band = next((b for b in LEVEL_ELO_RANGES if b[0] == level), None)
    if not band:
        return 0.0, elo, None
    _, lo, hi = band
    span = max(hi - lo, 1)
    frac = max(0.0, min(1.0, (elo - lo) / span))
    next_min = next((b[1] for b in LEVEL_ELO_RANGES if b[0] == level + 1), None)
    return frac, lo, next_min
