"""Consistent user-facing HTML for FACEIT errors and empty states (Telegram HTML parse mode)."""

from __future__ import annotations

from faceit_api import (
    FaceitAPIError,
    FaceitCircuitOpenError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from ui_text import bold, italic


def html_faceit_unavailable() -> str:
    return (
        bold("FACEIT is not responding right now.")
        + "\n"
        + italic("Their API may be overloaded or down. Try again in a few minutes.")
    )


def html_faceit_rate_limit() -> str:
    return (
        bold("FACEIT rate limit.")
        + "\n"
        + italic("Too many requests. Wait about a minute, then try again.")
    )


def html_faceit_circuit() -> str:
    return (
        bold("FACEIT requests paused briefly.")
        + "\n"
        + italic(
            "The bot hit repeated errors and is cooling off so we do not overload FACEIT. "
            "Try again in about a minute."
        )
    )


def html_faceit_api_generic() -> str:
    return (
        bold("Could not complete the FACEIT request.")
        + "\n"
        + italic("Try again later.")
    )


def html_faceit_transport_error(exc: BaseException) -> str:
    """Map transport-layer FACEIT errors to a single user string."""
    if isinstance(exc, FaceitCircuitOpenError):
        return html_faceit_circuit()
    if isinstance(exc, FaceitRateLimitError):
        return html_faceit_rate_limit()
    if isinstance(exc, FaceitUnavailableError):
        return html_faceit_unavailable()
    if isinstance(exc, FaceitAPIError):
        return html_faceit_api_generic()
    return html_faceit_api_generic()


def html_stats_form_empty() -> str:
    """When the form strip has no tiles (no recent results in the fetched batch)."""
    return italic(
        "No recent match results in this window yet. The strip fills as FACEIT returns games."
    )


def html_matches_list_empty_faceit() -> str:
    return italic("No recent matches returned by FACEIT for this account.")
