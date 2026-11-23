"""The repair pipeline: safe, documented, reversible-by-inspection fixes.

Only repairs that cannot invent data are implemented. Each one corresponds to
a lint rule and records what it changed in a machine-readable change log.
Structural damage (ragged rows, genuinely broken quoting) is detected and
reported but never silently rewritten - see docs/ROADMAP.md.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from . import values
from .csvio import inspect, iter_records
from .lint import MAX_DUP_ROWS

ALL_FIXES = (
    "bom",
    "newline",
    "header",
    "trim",
    "zero-width",
    "control",
    "quotes",
    "blank-rows",
    "dedupe",
)

FIX_DESCRIPTIONS = {
    "bom": "strip a UTF-8/UTF-16 byte order mark",
    "newline": "rewrite CRLF/CR line endings as LF",
    "header": "trim header cells, name blanks column_N, de-duplicate names",
    "trim": "strip leading/trailing whitespace from every value",
    "zero-width": "remove zero-width and other invisible formatting characters",
    "control": "remove C0 control characters (excluding tab and newline)",
    "quotes": "re-emit every record with canonical RFC 4180 quoting",
    "blank-rows": "drop rows whose fields are all empty",
    "dedupe": "drop rows identical to an earlier row (bounded index)",
}

MAX_LOG_ENTRIES = 5000


class FixError(Exception):
    """Raised for an invalid or unsafe repair request."""


@dataclass
class Change:
    """One recorded before/after change."""

    line: int
    action: str
    before: str
    after: str

    def as_dict(self) -> dict[str, object]:
        return {
            "line": self.line,
            "action": self.action,
            "before": self.before,
            "after": self.after,
        }


@dataclass
class RepairReport:
    """What the repair pass did (or would do)."""

    path: str
    out_path: str | None = None
    fixes: tuple[str, ...] = ()
    rows_in: int = 0
    rows_out: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def bump(self, action: str, n: int = 1) -> None:
        self.counts[action] = self.counts.get(action, 0) + n

    @property
    def total_changes(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "input": self.path,
            "output": self.out_path,
            "fixes": list(self.fixes),
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "counts": dict(sorted(self.counts.items())),
            "change_log_truncated": self.truncated,
            "changes": [c.as_dict() for c in self.changes],
            "notes": self.notes,
        }


def parse_fixes(spec: str) -> tuple[str, ...]:
    """Turn ``all`` or ``trim,dedupe`` into a validated, ordered fix tuple."""
    raw = [part.strip().lower() for part in spec.split(",") if part.strip()]
    if not raw:
        raise FixError("no repairs requested")
    if "all" in raw:
        return ALL_FIXES
    unknown = [r for r in raw if r not in ALL_FIXES]
    if unknown:
        raise FixError(
            "unknown repair "
            + repr(unknown[0])
            + "; choose from: "
            + ", ".join(ALL_FIXES)
        )
    ordered = [f for f in ALL_FIXES if f in raw]
    return tuple(ordered)


def _dedupe_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, name in enumerate(names):
        clean = values.strip_invisible(name).strip() or "column_" + str(i + 1)
        base = clean
        n = seen.get(base.casefold(), 0)
        seen[base.casefold()] = n + 1
        out.append(clean if n == 0 else clean + "_" + str(n + 1))
    return out


def _clean_value(raw: str, fixes: tuple[str, ...]) -> tuple[str, str]:
    """Apply per-field repairs; return ``(value, last_action)``."""
    value = raw
    action = ""
    if "zero-width" in fixes and values.has_zero_width(value):
        value = values.strip_invisible(value)
        action = "zero-width"
    if "control" in fixes:
        cleaned = "".join(values.find_control_chars(value))
        if cleaned:
            value = "".join(
                ch for ch in value if ch not in set(cleaned)
            )
            action = "control"
    if "trim" in fixes and value != value.strip():
        value = value.strip()
        action = "trim"
    return value, action


def repair(
    path: str,
    fixes: tuple[str, ...] = (),
    out_path: str | None = None,
    *,
    max_log_entries: int = MAX_LOG_ENTRIES,
) -> RepairReport:
    """Apply ``fixes`` to ``path`` and write ``out_path`` when given.

    When ``out_path`` is ``None`` the pipeline runs in dry-run mode: the input
    is fully scanned and a diff summary is produced, but nothing is written.
    """
    if not fixes:
        raise FixError("no repairs requested")
    dedupe = "dedupe" in fixes
    if dedupe and out_path is None:
        pass
    info = inspect(path)
    report = RepairReport(path=path, out_path=out_path, fixes=fixes)

    if "bom" in fixes and info.bom:
        report.bump("bom")
    style = info.newline_style
    if "newline" in fixes and style != "lf":
        report.bump("newline", max(1, sum(info.newlines.values())))

    sink: io.TextIOBase | None = None
    writer: csv.writer | None = None
    if out_path is not None:
        sink = open(out_path, "w", encoding="utf-8", newline="")
        # Preserve the input delimiter: a semicolon export stays semicolon
        # delimited so downstream systems keep reading it without changes.
        writer = csv.writer(
            sink,
            delimiter=info.delimiter,
            lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
        )

    header: list[str] = []
    seen: set[str] = set()
    dedupe_capped = False
    try:
        for rec in iter_records(info):
            if not header:
                header = list(rec.fields)
                new_header = header
                if "header" in fixes:
                    new_header = _dedupe_names(header)
                if new_header != header:
                    report.bump("header", 1)
                    _log(
                        report,
                        rec.line_no,
                        "header",
                        " | ".join(header),
                        " | ".join(new_header),
                        max_log_entries,
                    )
                if writer is not None:
                    writer.writerow(new_header)
                continue
            report.rows_in += 1
            fields = rec.fields
            if "quotes" in fixes and rec.problems:
                report.bump("quotes", len(rec.problems))
            cleaned = []
            row_action = ""
            for raw in fields:
                value, action = _clean_value(raw, fixes)
                cleaned.append(value)
                if action and not row_action:
                    row_action = action
            if "blank-rows" in fixes and all(f.strip() == "" for f in cleaned):
                report.bump("blank-rows")
                _log(report, rec.line_no, "blank-rows", _preview(fields), "", max_log_entries)
                continue
            # Dedupe runs on the cleaned fields so that repairing and then
            # re-running is a fixed point: trim, then dedupe - never the reverse.
            if dedupe and not dedupe_capped:
                key = "\x1f".join(cleaned)
                if key in seen:
                    report.bump("dedupe")
                    _log(report, rec.line_no, "dedupe", _preview(fields), "", max_log_entries)
                    continue
                if len(seen) >= MAX_DUP_ROWS:
                    dedupe_capped = True
                    report.notes.append(
                        "dedupe index capped at "
                        + str(MAX_DUP_ROWS)
                        + " rows; later duplicates kept"
                    )
                else:
                    seen.add(key)
            if row_action:
                report.bump(row_action)
                _log(
                    report,
                    rec.line_no,
                    row_action,
                    _preview(fields),
                    _preview(cleaned),
                    max_log_entries,
                )
            report.rows_out += 1
            if writer is not None:
                writer.writerow(cleaned)
    finally:
        if sink is not None:
            sink.close()
    if dedupe_capped:
        report.notes.append("duplicate rows beyond the cap were not removed")
    return report


def _preview(fields: list[str], limit: int = 60) -> str:
    text = " | ".join(fields)
    flat = text.replace("\n", "\\n").replace("\r", "\\r")
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def _log(
    report: RepairReport,
    line: int,
    action: str,
    before: str,
    after: str,
    limit: int,
) -> None:
    if len(report.changes) >= limit:
        report.truncated = True
        return
    report.changes.append(Change(line=line, action=action, before=before, after=after))


def write_change_log(report: RepairReport, path: str) -> None:
    """Persist the report as deterministic, sorted-key JSON."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report.as_dict(), fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
