"""Tests for faceit_messages.html_faceit_transport_error mapping."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from faceit_api import (
    FaceitAPIError,
    FaceitCircuitOpenError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from faceit_messages import html_faceit_transport_error


class TestHtmlFaceitTransportError:
    def test_rate_limit(self):
        s = html_faceit_transport_error(FaceitRateLimitError("x"))
        assert "rate limit" in s.lower()

    def test_unavailable(self):
        s = html_faceit_transport_error(FaceitUnavailableError("x"))
        assert "not responding" in s.lower() or "unavailable" in s.lower()

    def test_circuit(self):
        s = html_faceit_transport_error(FaceitCircuitOpenError("x"))
        assert "paused" in s.lower()

    def test_generic_api(self):
        s = html_faceit_transport_error(FaceitAPIError("x"))
        assert "faceit" in s.lower() or "request" in s.lower()
