"""Terminal rendering: ANSI colour, boxes and tables.

Colour is opt-out via the ``NO_COLOR`` environment variable and opt-in when
stdout is a TTY (or ``FORCE_COLOR`` is set, which is what the screenshot
helper uses). Every renderer returns a string so tests can assert on it.
"""

from __future__ import annotations

import os
import sys

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
GREY = "\x1b[90m"
BRIGHT_RED = "\x1b[91m"
BRIGHT_GREEN = "\x1b[92m"
BRIGHT_YELLOW = "\x1b[93m"
BRIGHT_BLUE = "\x1b[94m"

SEVERITY_COLOUR = {"error": BRIGHT_RED, "warn": BRIGHT_YELLOW, "info": BRIGHT_BLUE}
SEVERITY_LABEL = {"error": "ERROR", "warn": "WARN", "info": "INFO"}


def colour_enabled(stream=None) -> bool:
    """Respect NO_COLOR, then FORCE_COLOR, then fall back to isatty()."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def paint(text: str, *codes: str, enabled: bool = True) -> str:
    """Wrap ``text`` in ANSI codes when colour is enabled."""
    if not enabled or not codes:
        return text
    return "".join(codes) + text + RESET


def visible_len(text: str) -> int:
    """Length of ``text`` ignoring ANSI escape sequences."""
    total = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b" and i + 1 < n and text[i + 1] == "[":
            i += 2
            while i < n and text[i] != "m":
                i += 1
            i += 1
            continue
        total += 1
        i += 1
    return total


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad to a display width, ignoring ANSI codes."""
    gap = max(0, width - visible_len(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def table(
    headers: list[str],
    rows: list[list[str]],
    *,
    aligns: list[str] | None = None,
    max_widths: list[int] | None = None,
    enabled: bool = True,
) -> str:
    """Render a box-drawing table with ANSI-aware alignment."""
    if aligns is None:
        aligns = ["left"] * len(headers)
    if max_widths is None:
        max_widths = [0] * len(headers)
    widths = [visible_len(h) for h in headers]
    cells: list[list[str]] = []
    for row in rows:
        line = []
        for i in range(len(headers)):
            cell = row[i] if i < len(row) else ""
            limit = max_widths[i] if i < len(max_widths) else 0
            if limit and visible_len(cell) > limit:
                plain = strip_ansi(cell)
                cell = plain[: limit - 1] + "\u2026"
            line.append(cell)
            widths[i] = max(widths[i], visible_len(cell))
        cells.append(line)
    head_codes = (BOLD, CYAN) if enabled else ()
    top = "  " + "".join("\u2500" * (w + 2) for w in widths)
    out = [top]
    out.append(
        "  "
        + "  ".join(paint(pad(h, widths[i]), *head_codes) for i, h in enumerate(headers))
    )
    out.append("  " + "  ".join("\u2500" * (w + 2) for w in widths))
    for line in cells:
        out.append(
            "  "
            + "  ".join(
                pad(line[i], widths[i], aligns[i] if i < len(aligns) else "left")
                for i in range(len(widths))
            )
        )
    return "\n".join(out)


def strip_ansi(text: str) -> str:
    """Remove every ANSI SGR sequence from ``text``."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b" and i + 1 < n and text[i + 1] == "[":
            i += 2
            while i < n and text[i] != "m":
                i += 1
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def bar(value: int, total: int, width: int = 24, enabled: bool = True) -> str:
    """A simple proportional bar used by the summary block."""
    if total <= 0:
        filled = 0
    else:
        filled = round(width * value / total)
        filled = max(1, filled) if value else 0
    return paint("\u2588" * filled, CYAN, enabled=enabled) + paint(
        "\u2591" * (width - filled), GREY, enabled=enabled
    )

