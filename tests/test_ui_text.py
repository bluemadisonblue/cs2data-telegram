"""Tests for ui_text.py: HTML escape helpers."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui_text import bold, bullet_line, code, esc, italic, link, section, sep, spacer, tip_item


class TestEsc:
    def test_less_than(self):
        assert "&lt;" in esc("<")

    def test_greater_than(self):
        assert "&gt;" in esc(">")

    def test_ampersand(self):
        assert "&amp;" in esc("&")

    def test_none_returns_empty(self):
        assert esc(None) == ""

    def test_integer(self):
        assert esc(42) == "42"

    def test_float(self):
        assert esc(1.5) == "1.5"

    def test_plain_text_unchanged(self):
        assert esc("hello world") == "hello world"

    def test_combined_special_chars(self):
        result = esc("<b>hello & world</b>")
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result
        assert "hello" in result


class TestBold:
    def test_wraps_in_b_tags(self):
        result = bold("test")
        assert result == "<b>test</b>"

    def test_escapes_content(self):
        result = bold("<evil>")
        assert "<evil>" not in result
        assert "&lt;evil&gt;" in result


class TestItalic:
    def test_wraps_in_i_tags(self):
        assert italic("hello") == "<i>hello</i>"

    def test_escapes_content(self):
        result = italic("a & b")
        assert "&amp;" in result


class TestCode:
    def test_wraps_in_code_tags(self):
        assert code("snippet") == "<code>snippet</code>"

    def test_escapes_content(self):
        result = code("<script>")
        assert "<script>" not in result


class TestSection:
    def test_format(self):
        result = section("🎯", "Overview")
        assert "🎯" in result
        assert "<b>Overview</b>" in result

    def test_escapes_title(self):
        result = section("⚠️", "A & B")
        assert "&amp;" in result


class TestSep:
    def test_default_width(self):
        result = sep()
        # Default width=24, wrapped in code tags
        assert "<code>" in result
        assert "·" * 24 in result

    def test_custom_width(self):
        result = sep(10)
        assert "·" * 10 in result

    def test_width_zero(self):
        result = sep(0)
        assert "<code>" in result


class TestLink:
    def test_basic_link(self):
        result = link("https://example.com", "Click here")
        assert 'href="https://example.com"' in result
        assert "Click here" in result

    def test_escapes_url(self):
        result = link('https://x.com?a=1&b=2', "Link")
        assert "&amp;" in result

    def test_escapes_text(self):
        result = link("https://x.com", "<b>bold</b>")
        assert "<b>bold</b>" not in result  # raw tags should be escaped


class TestBulletLine:
    def test_prepends_bullet(self):
        assert bullet_line("item") == "• item"

    def test_empty_string(self):
        assert bullet_line("") == "• "


class TestSpacer:
    def test_returns_empty_string(self):
        assert spacer() == ""


class TestTipItem:
    def test_plain_only(self):
        assert tip_item(esc("hello")) == "<i>• hello</i>"

    def test_mixed_code(self):
        s = tip_item(esc("run "), code("/help"), esc(" now"))
        assert s.startswith("<i>• ")
        assert s.endswith("</i>")
        assert "<code>/help</code>" in s
        assert "run " in s
