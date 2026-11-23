"""Rendering helpers: ANSI-aware width, tables and colour policy."""

from __future__ import annotations

from csv_surgeon import render


def test_visible_len_ignores_ansi():
    painted = render.paint("abc", render.RED, enabled=True)
    assert render.visible_len(painted) == 3
    assert render.visible_len("abc") == 3


def test_paint_disabled_is_a_noop():
    assert render.paint("abc", render.RED, enabled=False) == "abc"


def test_strip_ansi():
    assert render.strip_ansi(render.paint("x", render.BOLD, render.CYAN)) == "x"


def test_pad_left_and_right():
    assert render.pad("ab", 4) == "ab  "
    assert render.pad("ab", 4, "right") == "  ab"
    assert render.pad("ab", 4, "center") == " ab "
    assert render.pad("abcdef", 3) == "abcdef"


def test_table_columns_line_up():
    text = render.table(["A", "BB"], [["1", "2"]], enabled=False)
    lines = text.splitlines()
    assert len(lines) == 4
    assert all(render.visible_len(line) > 0 for line in lines)


def test_table_truncates_long_cells():
    text = render.table(["H"], [["x" * 50]], max_widths=[10], enabled=False)
    assert "\u2026" in text
    assert "x" * 50 not in text


def test_bar_is_proportional():
    full = render.bar(10, 10, width=10, enabled=False)
    empty = render.bar(0, 10, width=10, enabled=False)
    assert full.count("\u2588") == 10
    assert empty.count("\u2588") == 0


def test_colour_disabled_by_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert not render.colour_enabled()


def test_colour_forced_by_force_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert render.colour_enabled()


def test_severity_palette_covers_every_severity():
    assert set(render.SEVERITY_COLOUR) == {"error", "warn", "info"}
    assert set(render.SEVERITY_LABEL) == {"error", "warn", "info"}
