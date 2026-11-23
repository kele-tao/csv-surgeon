"""Per-column profiling.

One streaming pass collects the type mix, null patterns, top values,
distinct-count and longest values for each column. Distinct tracking is
capped so memory stays flat on pathological columns; when the cap is hit the
column is reported as capped rather than with a wrong exact number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import values
from .csvio import StreamInfo, inspect, iter_records

TOP_N = 10
MAX_DISTINCT = 50_000
LONGEST_PREVIEW = 72


def _null_kind(raw: str) -> str | None:
    text = raw.strip()
    if raw == "":
        return "empty"
    if text in ("NULL", "null", "None", "none"):
        return "NULL/None"
    if text in ("N/A", "NA", "na", "#N/A"):
        return "N/A"
    if text == "-":
        return "dash"
    if text.lower() == "nan":
        return "nan"
    return None


@dataclass
class ColumnProfile:
    """Statistics for a single column."""

    index: int
    name: str
    types: dict[str, int] = field(default_factory=dict)
    nulls: dict[str, int] = field(default_factory=dict)
    distinct: int = 0
    distinct_capped: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    display: dict[str, str] = field(default_factory=dict)
    longest: tuple[int, str] = (0, "")
    min_value: str = ""
    max_value: str = ""
    near_duplicates: list[tuple[str, list[str]]] = field(default_factory=list)
    whitespace: int = 0
    invisible: int = 0

    @property
    def dominant_type(self) -> str:
        if not self.types:
            return "empty"
        return max(self.types.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def mixed(self) -> bool:
        return len([t for t in self.types if t != "empty"]) > 1

    def top(self, n: int = TOP_N) -> list[tuple[str, int]]:
        """The n most frequent values, ties broken alphabetically."""
        items = sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(self.display.get(key, key), count) for key, count in items[:n]]

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "name": self.name,
            "type": self.dominant_type,
            "types": dict(sorted(self.types.items())),
            "nulls": dict(sorted(self.nulls.items())),
            "distinct": self.distinct,
            "distinct_capped": self.distinct_capped,
            "top": [{"value": v, "count": c} for v, c in self.top()],
            "longest": {"length": self.longest[0], "preview": self.longest[1]},
            "min": self.min_value,
            "max": self.max_value,
            "near_duplicates": [{"key": k, "spellings": v} for k, v in self.near_duplicates],
            "whitespace": self.whitespace,
            "invisible": self.invisible,
        }


@dataclass
class ProfileReport:
    """Result of :func:`profile`."""

    path: str
    info: StreamInfo
    rows: int = 0
    header: list[str] = field(default_factory=list)
    columns: list[ColumnProfile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "rows": self.rows,
            "columns": len(self.header),
            "encoding": self.info.encoding_guess.label if self.info.encoding_guess else "",
            "delimiter": self.info.delimiter,
            "header": self.header,
            "column_profiles": [c.as_dict() for c in self.columns],
            "notes": self.notes,
        }


def _classify(raw: str) -> str:
    if raw == "":
        return "empty"
    d = values.classify_date(raw)
    if d is not None:
        return "datetime" if d.has_time else "date"
    n = values.classify_number(raw)
    if n is not None:
        return n.kind
    low = raw.strip().lower()
    if low in ("true", "false", "yes", "no", "y", "n"):
        return "bool"
    return "text"


def _preview(raw: str, limit: int = LONGEST_PREVIEW) -> str:
    flat = raw.replace("\n", "\\n").replace("\r", "\\r")
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def _numeric(raw: str) -> float | None:
    info = values.classify_number(raw)
    if info is None:
        return None
    return info.value


def _update_minmax(col: ColumnProfile, raw: str, kind: str) -> None:
    """Track the smallest and largest value, numerically for numbers and dates."""
    if not col.min_value:
        col.min_value = raw
        col.max_value = raw
        return
    if kind in ("int", "float", "currency", "percent"):
        a, b, c = _numeric(raw), _numeric(col.min_value), _numeric(col.max_value)
        if a is not None and b is not None and c is not None:
            if a < b:
                col.min_value = raw
            if a > c:
                col.max_value = raw
            return
    if kind in ("date", "datetime"):
        da = values.classify_date(raw)
        db = values.classify_date(col.min_value)
        dc = values.classify_date(col.max_value)
        if da and db and dc and da.value and db.value and dc.value:
            if da.value < db.value:
                col.min_value = raw
            if da.value > dc.value:
                col.max_value = raw
            return
    if raw < col.min_value:
        col.min_value = raw
    if raw > col.max_value:
        col.max_value = raw


def _track_value(
    col: ColumnProfile,
    pool: dict[str, set[str]],
    raw: str,
    max_distinct: int,
) -> None:
    """Count exact values and remember case/space variants for near-duplicates."""
    key = values.normalise_value(raw)
    known = pool.get(key)
    if known is None:
        if len(pool) >= max_distinct:
            col.distinct_capped = True
            return
        pool[key] = {raw}
    elif raw not in known:
        known.add(raw)
    if raw in col.counts:
        col.counts[raw] += 1
        return
    if col.distinct_capped:
        return
    if len(col.counts) >= max_distinct:
        col.distinct_capped = True
        return
    col.counts[raw] = 1
    col.display[raw] = raw
    col.distinct = len(col.counts)


def profile(path: str, *, top_n: int = TOP_N, max_distinct: int = MAX_DISTINCT) -> ProfileReport:
    """Profile every column of ``path`` in a single streaming pass."""
    info = inspect(path)
    report = ProfileReport(path=path, info=info)
    header: list[str] = []
    columns: list[ColumnProfile] = []
    normalised: list[dict[str, set[str]]] = []
    rows = 0
    for rec in iter_records(info):
        if not header:
            header = rec.fields
            report.header = header
            for i, name in enumerate(header):
                columns.append(
                    ColumnProfile(index=i, name=values.strip_invisible(name).strip())
                )
                normalised.append({})
            report.columns = columns
            continue
        rows += 1
        for i, raw in enumerate(rec.fields):
            if i >= len(columns):
                break
            col = columns[i]
            kind = _classify(raw)
            col.types[kind] = col.types.get(kind, 0) + 1
            null_kind = _null_kind(raw)
            if null_kind is not None:
                col.nulls[null_kind] = col.nulls.get(null_kind, 0) + 1
            if raw != raw.strip():
                col.whitespace += 1
            if values.has_zero_width(raw):
                col.invisible += 1
            if len(raw) > col.longest[0]:
                col.longest = (len(raw), _preview(raw))
            if kind != "empty":
                _update_minmax(col, raw, kind)
                _track_value(col, normalised[i], raw, max_distinct)
    report.rows = rows
    for i, col in enumerate(columns):
        groups = []
        for key, spellings in normalised[i].items():
            if len(spellings) > 1:
                groups.append((key, sorted(spellings)[:4]))
        groups.sort(key=lambda g: g[0])
        col.near_duplicates = groups[:5]
        if col.distinct_capped:
            report.notes.append(
                "column "
                + col.name
                + ": distinct count capped at "
                + str(max_distinct)
                + " (value is a lower bound)"
            )
    return report
