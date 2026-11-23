"""Value-level classifiers: numbers, currencies, dates and invisible characters.

These helpers are the shared vocabulary of the linter, the profiler and the
SQL type inference. They are deterministic and locale-free: ambiguous decimal
formats are reported, never silently reinterpreted.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

EMPTY = ""

NULL_TOKENS = frozenset(
    {"", "null", "NULL", "Null", "n/a", "N/A", "NA", "na", "#N/A", "-", "None", "none", "nan"}
)

INT_RE = re.compile(r"^[+-]?[0-9]+$")
GROUPED_RE = re.compile(r"^[+-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?$")
DOTTED_GROUP_RE = re.compile(r"^[+-]?[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?$")
DECIMAL_RE = re.compile(r"^[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?$")
CURRENCY_RE = re.compile(r"^(?P<sym>[$\u20ac\u00a3\u00a5])\s*(?P<num>[0-9][0-9.,]*)$")
PERCENT_RE = re.compile(r"^(?P<num>[+-]?[0-9]+(?:\.[0-9]+)?)\s*%$")
PAREN_NEG_RE = re.compile(r"^\((?P<num>[0-9][0-9.,]*)\)$")

ISO_DATE_RE = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$")
SLASH_DMY_RE = re.compile(r"^(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})$")
SLASH_MDY_RE = re.compile(r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})$")
DOTTED_DMY_RE = re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})$")
ISO_DATETIME_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})[T ](?P<H>\d{2}):(?P<M>\d{2})"
    r"(?::(?P<S>\d{2}))?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_NAME_RE = re.compile(
    r"^(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2}),?\s+(?P<y>\d{4})$"
)
DAY_MONTH_NAME_RE = re.compile(
    r"^(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<y>\d{4})$"
)

ZERO_WIDTH = frozenset(
    [
        "\u200b",  # zero width space
        "\u200c",  # zero width non-joiner
        "\u200d",  # zero width joiner
        "\u2060",  # word joiner
        "\ufeff",  # zero width no-break space / stray BOM
        "\u00ad",  # soft hyphen
    ]
)


def has_zero_width(value: str) -> bool:
    """True if the value contains any invisible formatting character."""
    return any(ch in ZERO_WIDTH for ch in value)


def strip_invisible(value: str) -> str:
    """Remove zero-width and other invisible formatting characters."""
    return "".join(ch for ch in value if ch not in ZERO_WIDTH)


def find_control_chars(value: str) -> list[str]:
    """Return the control characters (U+0000-U+001F, U+007F) in a value.

    Tab and newline are only reported by the caller when they are not the
    record separator, so they are excluded here.
    """
    return [ch for ch in value if (ord(ch) < 32 or ord(ch) == 127) and ch not in "\t\n\r"]


@dataclass(frozen=True)
class NumberInfo:
    """Outcome of :func:`classify_number`."""

    kind: str  # int | float | currency | percent | paren-negative | ambiguous | None
    value: float | None
    note: str = ""


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def classify_number(value: str) -> NumberInfo | None:
    """Classify a raw cell as a number, currency or percent value.

    Returns ``None`` when the cell is not numeric at all. ``kind="ambiguous"``
    means the string is consistent with two locales (``1.234`` and ``1,234``).
    """
    text = value.strip()
    if not text:
        return None
    if INT_RE.match(text):
        return NumberInfo("int", float(text))
    if DECIMAL_RE.match(text):
        return NumberInfo("float", _to_float(text))
    if GROUPED_RE.match(text):
        return NumberInfo("float", _to_float(text.replace(",", "")), "thousands separator ','")
    if DOTTED_GROUP_RE.match(text):
        return NumberInfo(
            "float", _to_float(text.replace(".", "").replace(",", ".")), "thousands separator '.'"
        )
    m = CURRENCY_RE.match(text)
    if m:
        num = m.group("num")
        parsed = classify_number(num)
        if parsed is not None and parsed.value is not None:
            return NumberInfo("currency", parsed.value, "symbol " + m.group("sym"))
        return NumberInfo("ambiguous", None, "currency-like but not parseable")
    m = PERCENT_RE.match(text)
    if m:
        return NumberInfo("percent", _to_float(m.group("num")), "percent, not a ratio")
    m = PAREN_NEG_RE.match(text)
    if m:
        inner = classify_number(m.group("num"))
        if inner is not None and inner.value is not None:
            return NumberInfo("paren-negative", -inner.value, "accounting negative")
    return None


def ambiguous_number(value: str) -> str | None:
    """Return a note when ``1.234`` / ``1,234`` style separators are ambiguous.

    A string is ambiguous when a single separator appears with exactly three
    digits after it at least once - that is a thousands separator in one
    locale and a decimal mark in another. A later separator disambiguates.
    """
    if "," not in value and "." not in value:
        return None
    text = value.strip()
    if text[:1] in "$\u20ac\u00a3\u00a5":
        text = text[1:].strip()
    if not text or not any(c.isdigit() for c in text):
        return None
    if text.count(",") > 1 or text.count(".") > 1:
        if text.rfind(",") > text.rfind("."):
            return "repeated separators, decimal comma assumed"
        return "repeated separators, decimal point assumed"
    if text.count(",") and text.count("."):
        return None
    sep = "," if "," in text else "." if "." in text else ""
    if not sep:
        return None
    parts = text.split(sep)
    if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
        return "1" + sep + "234 pattern: thousands separator or decimal mark"
    return None


@dataclass(frozen=True)
class DateInfo:
    """Outcome of :func:`classify_date`."""

    pattern: str
    value: _dt.date | None
    has_time: bool = False


def _mkdate(y: int, m: int, d: int) -> _dt.date | None:
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def _month(name: str) -> int | None:
    return MONTHS.get(name.lower().rstrip("."))


DATE_SHAPE_RE = re.compile(
    r"^(?:\d{4}-\d{1,2}-\d{1,2}(?:[T ].*)?"
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4})$"
)


def classify_date(value: str) -> DateInfo | None:
    """Recognise the date formats that Excel exports actually contain.

    Supported patterns: ``ISO``, ``ISO_SHORT`` (``2024-1-2``), ``DMY_SLASH``,
    ``MDY_SLASH``, ``DMY_DOT``, ``MON_NAME``, ``DAY_MON_NAME``, ``ISO_DATETIME``.
    """
    text = value.strip()
    # One cheap shape check rejects numbers, names and ids before any of the
    # eight format regexes run; classify_date is the hottest function in lint.
    if len(text) < 8 or len(text) > 40 or DATE_SHAPE_RE.match(text) is None:
        return None
    m = ISO_DATETIME_RE.match(text)
    if m:
        base = _mkdate(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        if base is None:
            return None
        return DateInfo("ISO_DATETIME", base, True)
    m = ISO_DATE_RE.match(text)
    if m:
        base = _mkdate(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        if base is None:
            return None
        pattern = "ISO" if len(m.group("m")) == 2 and len(m.group("d")) == 2 else "ISO_SHORT"
        return DateInfo(pattern, base)
    m = SLASH_DMY_RE.match(text)
    if m:
        d, mo, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
        base = _mkdate(y, mo, d)
        if base is not None:
            if d > 12 >= mo:
                return DateInfo("DMY_SLASH", base)
            if mo > 12 >= d:
                return DateInfo("MDY_SLASH", base)
            return DateInfo("DMY_SLASH_OR_MDY", base)
    m = SLASH_MDY_RE.match(text)
    if m:
        mo, d, y = int(m.group("m")), int(m.group("d")), int(m.group("y"))
        base = _mkdate(y, mo, d)
        if base is not None:
            if d > 12 >= mo:
                return DateInfo("DMY_SLASH", base)
            if mo > 12 >= d:
                return DateInfo("MDY_SLASH", base)
            return DateInfo("DMY_SLASH_OR_MDY", base)
    m = DOTTED_DMY_RE.match(text)
    if m:
        base = _mkdate(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        if base is not None:
            return DateInfo("DMY_DOT", base)
    m = MONTH_NAME_RE.match(text)
    if m:
        mo = _month(m.group("mon"))
        if mo:
            base = _mkdate(int(m.group("y")), mo, int(m.group("d")))
            if base is not None:
                return DateInfo("MON_NAME", base)
    m = DAY_MONTH_NAME_RE.match(text)
    if m:
        mo = _month(m.group("mon"))
        if mo:
            base = _mkdate(int(m.group("y")), mo, int(m.group("d")))
            if base is not None:
                return DateInfo("DAY_MON_NAME", base)
    return None


def is_null_like(value: str) -> bool:
    """True for the empty string and the usual "no value" spellings."""
    return value.strip() in NULL_TOKENS


def normalise_value(value: str) -> str:
    """Case- and whitespace-insensitive key used for near-duplicate detection."""
    return " ".join(strip_invisible(value).split()).casefold()
DECIMAL_POINT_RE = re.compile(r"^[+-]?[0-9]+\.[0-9]{1,2}$|^[+-]?[0-9]+\.[0-9]{4,}$")
DECIMAL_COMMA_RE = re.compile(r"^[+-]?[0-9]+,[0-9]{1,2}$|^[+-]?[0-9]+,[0-9]{4,}$")
AMBIG_THREE_RE = re.compile(r"^[+-]?[0-9]{1,3}[.,][0-9]{3}$")
GROUP_COMMA_RE = re.compile(r"^[+-]?[0-9]{1,3}(?:,[0-9]{3})+$")
GROUP_DOT_RE = re.compile(r"^[+-]?[0-9]{1,3}(?:\.[0-9]{3})+$")

NUMBER_STYLE_NOTES = {
    "dot_decimal": "decimal point (1.23)",
    "comma_decimal": "decimal comma (1,23)",
    "comma_thousands": "comma thousands + dot decimal (1,234.56)",
    "dot_thousands": "dot thousands + comma decimal (1.234,56)",
    "ambiguous_1000": "single separator before 3 digits (1.234)",
}


def _digit_run_lengths(text: str, sep: str) -> tuple[int, int]:
    """Return (digits before the first sep, digits after the last sep)."""
    first = text.find(sep)
    last = text.rfind(sep)
    return first, len(text) - last - 1


def number_style(value: str) -> str | None:
    """Classify the separator convention of a numeric cell.

    Returns one of dot_decimal, comma_decimal, comma_thousands,
    dot_thousands, ambiguous_1000, or None when the cell is not a plain number.
    The distinction matters: 1,234.56 and 1.234,56 mean different things in
    different locales and a loader silently picks one of them.

    Written as a character scan rather than a regex battery because this runs
    once per numeric cell on every lint pass.
    """
    text = value.strip()
    if not text:
        return None
    if text[0] in "$\u20ac\u00a3\u00a5":
        text = text[1:].lstrip()
    if text.endswith("%"):
        text = text[:-1].rstrip()
    if text[:1] == "(" and text[-1:] == ")":
        text = text[1:-1]
    if not text:
        return None
    has_comma = "," in text
    has_dot = "." in text
    if not has_comma and not has_dot:
        return None
    digits = 0
    for ch in text:
        if ch.isdigit():
            digits += 1
        elif ch in "+-":
            continue
        elif ch == "," or ch == ".":
            continue
        else:
            return None
    if digits < 1:
        return None
    if has_comma and has_dot:
        return "comma_thousands" if text.rfind(",") < text.rfind(".") else "dot_thousands"
    sep = "," if has_comma else "."
    before, after = _digit_run_lengths(text, sep)
    if text.count(sep) == 1 and after == 3 and 0 < before <= 3:
        return "ambiguous_1000"
    if after in (1, 2) or after >= 4:
        return "comma_decimal" if sep == "," else "dot_decimal"
    if after == 3 and before <= 3:
        return "comma_thousands" if sep == "," else "dot_thousands"
    return None


DOT_FAMILY = frozenset({"dot_decimal", "dot_thousands"})
COMMA_FAMILY = frozenset({"comma_decimal", "comma_thousands"})
