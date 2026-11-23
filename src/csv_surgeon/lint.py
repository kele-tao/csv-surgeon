"""The lint rule engine.

Every finding carries a stable rule code (``CS001``-``CS015``), a severity,
the physical line and the 1-based column where it was seen, and a count so a
10 million row export produces a report, not a flood. The engine is streaming:
memory is O(columns + capped duplicate index), never O(rows).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import values
from .csvio import Record, StreamInfo, inspect, iter_records

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class Rule:
    """Documented description of one check."""

    code: str
    title: str
    severity: str
    fixable: bool
    description: str


RULES: dict[str, Rule] = {
    "CS001": Rule(
        "CS001", "Duplicate header name", "warn", True,
        "Two columns share a name after trimming; tools silently shadow one of them.",
    ),
    "CS002": Rule(
        "CS002", "Blank or padded header", "warn", True,
        "A header cell is empty or has leading/trailing whitespace.",
    ),
    "CS003": Rule(
        "CS003", "RFC 4180 quote violation", "error", False,
        "A quote appears inside an unquoted field, or text follows a closing quote.",
    ),
    "CS004": Rule(
        "CS004", "Ragged row", "error", False,
        "A data row has a different field count than the header.",
    ),
    "CS005": Rule(
        "CS005", "Byte order mark present", "info", True,
        "A UTF-8/UTF-16 BOM prefixes the file; some parsers leak it into column 1.",
    ),
    "CS006": Rule(
        "CS006", "Non-LF or mixed line endings", "info", True,
        "CRLF or a mix of CRLF/LF/CR endings; normalising keeps diffs stable.",
    ),
    "CS007": Rule(
        "CS007", "Leading or trailing whitespace in values", "warn", True,
        "Values only match after trimming, which breaks joins and group-bys.",
    ),
    "CS008": Rule(
        "CS008", "Invisible zero-width characters", "warn", True,
        "Zero-width spaces, joiners and soft hyphens invisible in Excel.",
    ),
    "CS009": Rule(
        "CS009", "Control characters", "error", True,
        "C0/DEL control bytes that break terminals, JSON and most loaders.",
    ),
    "CS010": Rule(
        "CS010", "Mixed date formats in one column", "warn", False,
        "More than one date spelling in the same column, e.g. ISO and DMY.",
    ),
    "CS011": Rule(
        "CS011", "Ambiguous or mixed number formats", "warn", False,
        "Thousands/decimal separators that differ between locales, or 1.234 style values.",
    ),
    "CS012": Rule(
        "CS012", "Duplicate rows", "warn", True,
        "Rows whose fields are byte-identical to an earlier row.",
    ),
    "CS013": Rule(
        "CS013", "Embedded newline inside a quoted field", "info", False,
        "Legal CSV, but it breaks line-oriented tools such as awk and grep.",
    ),
    "CS014": Rule(
        "CS014", "Unicode normalisation mismatch", "warn", True,
        "Visually identical text stored as NFC in one row and NFD in another.",
    ),
    "CS015": Rule(
        "CS015", "Near-duplicate values", "info", False,
        "Distinct values that collapse to the same key after case/space folding.",
    ),
}

MAX_EXAMPLES = 5
MAX_DUP_ROWS = 200_000
MAX_DISTINCT_TRACKED = 20_000
AMBIGUOUS_CAP = 1_000_000


@dataclass
class Finding:
    """One aggregated lint finding."""

    code: str
    severity: str
    message: str
    count: int = 1
    line: int | None = None
    column: int | None = None
    column_name: str = ""
    sample: str = ""
    examples: list[tuple[int, str]] = field(default_factory=list)

    @property
    def rule(self) -> Rule:
        return RULES[self.code]

    @property
    def fixable(self) -> bool:
        return self.rule.fixable

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "line": self.line,
            "column": self.column,
            "column_name": self.column_name,
            "sample": self.sample,
            "fixable": self.fixable,
            "examples": [{"line": ln, "value": v} for ln, v in self.examples],
        }


@dataclass
class LintReport:
    """Everything ``lint`` learned about one file."""

    path: str
    info: StreamInfo
    findings: list[Finding]
    rows: int = 0
    columns: int = 0
    header: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_severity(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    @property
    def error_count(self) -> int:
        return len(self.by_severity("error"))

    @property
    def warn_count(self) -> int:
        return len(self.by_severity("warn"))

    @property
    def info_count(self) -> int:
        return len(self.by_severity("info"))

    @property
    def total_instances(self) -> int:
        return sum(f.count for f in self.findings)

    def counts(self) -> dict[str, int]:
        """Severity counts plus the total number of individual instances."""
        return {
            "error": self.error_count,
            "warn": self.warn_count,
            "info": self.info_count,
            "instances": self.total_instances,
        }

    def rule_counts(self) -> list[tuple[str, str, int, bool]]:
        """``(code, severity, instances, fixable)`` sorted by code."""
        tally: dict[str, int] = {}
        for f in self.findings:
            tally[f.code] = tally.get(f.code, 0) + f.count
        return [
            (code, RULES[code].severity, tally[code], RULES[code].fixable)
            for code in sorted(tally)
        ]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "encoding": self.info.encoding_guess.label if self.info.encoding_guess else "",
            "delimiter": self.info.delimiter,
            "newlines": self.info.newline_style,
            "rows": self.rows,
            "columns": self.columns,
            "header": self.header,
            "counts": {
                "error": self.error_count,
                "warn": self.warn_count,
                "info": self.info_count,
                "instances": self.total_instances,
            },
            "findings": [f.as_dict() for f in self.findings],
            "notes": self.notes,
        }


class _ColumnState:
    """Bounded per-column accumulator used by the streaming pass."""

    __slots__ = (
        "ambiguous",
        "control",
        "date_patterns",
        "distinct",
        "distinct_overflow",
        "index",
        "name",
        "newline",
        "nfc",
        "nfd",
        "number_styles",
        "whitespace",
        "zero_width",
    )

    def __init__(self, index: int, name: str) -> None:
        self.index = index
        self.name = name
        self.whitespace = 0
        self.zero_width = 0
        self.control = 0
        self.newline = 0
        self.date_patterns: dict[str, int] = {}
        self.number_styles: dict[str, int] = {}
        self.ambiguous = 0
        self.distinct: dict[str, set[str]] = {}
        self.distinct_overflow = False
        self.nfc = 0
        self.nfd = 0


def _norm_header(header: list[str]) -> list[str]:
    out = []
    for i, name in enumerate(header):
        clean = values.strip_invisible(name).strip()
        out.append(clean if clean else "column_" + str(i + 1))
    return out


def lint(path: str, *, max_examples: int = MAX_EXAMPLES) -> LintReport:
    """Run every rule over ``path`` in a single streaming pass."""
    info = inspect(path)
    findings: list[Finding] = []
    notes: list[str] = []

    enc = info.encoding_guess
    if info.bom:
        findings.append(
            Finding(
                "CS005",
                RULES["CS005"].severity,
                "file starts with a " + (enc.label if enc else info.encoding) + " byte order mark",
                line=1,
                sample=info.bom.hex(" "),
            )
        )
    style = info.newline_style
    mixed = style == "mixed"
    if style != "lf":
        findings.append(
            Finding(
                "CS006",
                "warn" if mixed else "info",
                ("mixed line endings: " + _newline_desc(info))
                if mixed
                else ("line endings are " + style.upper() + ", not bare LF"),
                line=None,
            )
        )

    records = iter_records(info)
    header: list[str] = []
    columns: list[_ColumnState] = []
    rows = 0
    ragged = 0
    ragged_examples: list[tuple[int, str]] = []
    quoted_problems = 0
    quoted_examples: list[tuple[int, str]] = []
    dup_rows = 0
    dup_examples: list[tuple[int, str]] = []
    seen: set[bytes] = set()
    dedupe_truncated = False
    embedded_newlines = 0
    newline_examples: list[tuple[int, str]] = []

    first = True
    for rec in records:
        if first:
            first = False
            header = rec.fields
            columns = [_ColumnState(i, name) for i, name in enumerate(header)]
            _check_header(header, findings)
            continue
        rows += 1
        fields = rec.fields
        if len(fields) != len(header):
            ragged += 1
            if len(ragged_examples) < max_examples:
                ragged_examples.append(
                    (rec.line_no, str(len(fields)) + " fields, header has " + str(len(header)))
                )
        if rec.problems:
            quoted_problems += len(rec.problems)
            if len(quoted_examples) < max_examples:
                night = rec.problems[0]
                col_name = header[night.column] if night.column < len(header) else "?"
                quoted_examples.append(
                    (rec.line_no, "column " + col_name + ": " + night.message)
                )
        if rec.multiline:
            embedded_newlines += 1
            if len(newline_examples) < max_examples:
                newline_examples.append(
                    (
                        rec.line_no,
                        "record spans lines "
                        + str(rec.line_no)
                        + "-"
                        + str(rec.end_line),
                    )
                )
        if not dedupe_truncated:
            # The raw record text is already a unique fingerprint of the fields
            # and avoids re-joining and re-encoding every row.
            key = rec.raw
            if key in seen:
                dup_rows += 1
                if len(dup_examples) < max_examples:
                    dup_examples.append((rec.line_no, _preview(fields)))
            else:
                seen.add(key)
                if len(seen) >= MAX_DUP_ROWS:
                    dedupe_truncated = True
        _scan_values(rec, columns, header)

    if first:
        return LintReport(path=path, info=info, findings=findings, notes=["file is empty"])

    if ragged:
        findings.append(
            Finding(
                "CS004",
                RULES["CS004"].severity,
                str(ragged) + " of " + str(rows) + " data rows have the wrong field count",
                count=ragged,
                examples=ragged_examples,
                line=ragged_examples[0][0] if ragged_examples else None,
            )
        )
    if quoted_problems:
        findings.append(
            Finding(
                "CS003",
                RULES["CS003"].severity,
                str(quoted_problems) + " RFC 4180 quote violation(s)",
                count=quoted_problems,
                examples=quoted_examples,
                line=quoted_examples[0][0] if quoted_examples else None,
            )
        )
    if dup_rows:
        findings.append(
            Finding(
                "CS012",
                RULES["CS012"].severity,
                str(dup_rows) + " duplicate row(s) of " + str(rows),
                count=dup_rows,
                examples=dup_examples,
                line=dup_examples[0][0] if dup_examples else None,
            )
        )
    if dedupe_truncated:
        notes.append(
            "duplicate tracking stopped after "
            + str(MAX_DUP_ROWS)
            + " rows; later duplicates are not counted"
        )
    if embedded_newlines:
        findings.append(
            Finding(
                "CS013",
                RULES["CS013"].severity,
                str(embedded_newlines) + " record(s) contain an embedded newline",
                count=embedded_newlines,
                examples=newline_examples,
                line=newline_examples[0][0] if newline_examples else None,
            )
        )

    findings.extend(_column_findings(columns, rows, max_examples))
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.code, f.line or 0))
    return LintReport(
        path=path,
        info=info,
        findings=findings,
        rows=rows,
        columns=len(header),
        header=_norm_header(header),
        notes=notes,
    )


def _newline_desc(info: StreamInfo) -> str:
    parts = []
    for key, label in (("crlf", "CRLF"), ("lf", "LF"), ("cr", "CR")):
        n = info.newlines.get(key, 0)
        if n:
            parts.append(str(n) + " " + label)
    return ", ".join(parts)


def _preview(fields: list[str], limit: int = 60) -> str:
    text = " | ".join(fields)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _check_header(header: list[str], findings: list[Finding]) -> None:
    seen: dict[str, int] = {}
    duplicates = 0
    dup_examples: list[tuple[int, str]] = []
    blank = 0
    blank_examples: list[tuple[int, str]] = []
    for i, name in enumerate(header):
        clean = values.strip_invisible(name).strip()
        if not clean:
            blank += 1
            if len(blank_examples) < MAX_EXAMPLES:
                blank_examples.append((1, "column " + str(i + 1) + " is empty"))
        elif clean != name:
            blank += 1
            if len(blank_examples) < MAX_EXAMPLES:
                blank_examples.append((1, "column " + str(i + 1) + " is padded: " + repr(name)))
        key = clean.casefold()
        if key and key in seen:
            duplicates += 1
            if len(dup_examples) < MAX_EXAMPLES:
                dup_examples.append(
                    (
                        1,
                        "columns "
                        + str(seen[key] + 1)
                        + " and "
                        + str(i + 1)
                        + " are both "
                        + repr(clean),
                    )
                )
        else:
            seen[key] = i
    if duplicates:
        findings.append(
            Finding(
                "CS001",
                RULES["CS001"].severity,
                str(duplicates) + " duplicate header name(s)",
                count=duplicates,
                line=1,
                examples=dup_examples,
                sample=header[0] if header else "",
            )
        )
    if blank:
        findings.append(
            Finding(
                "CS002",
                RULES["CS002"].severity,
                str(blank) + " blank or padded header cell(s)",
                count=blank,
                line=1,
                examples=blank_examples,
            )
        )


FIRST_CHARS_NUMERIC = frozenset("0123456789+-($\u20ac\u00a3\u00a5.%")
CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _scan_values(rec: Record, columns: list[_ColumnState], header: list[str]) -> None:
    """Accumulate per-column evidence for one record.

    Hot loop: every check is guarded by the cheapest possible pre-test so a
    plain ASCII text cell costs two comparisons, not seven regexes.
    """
    for i, raw in enumerate(rec.fields):
        if i >= len(columns):
            break
        if not raw:
            continue
        col = columns[i]
        if raw != raw.strip():
            col.whitespace += 1
        ascii_only = raw.isascii()
        if not ascii_only:
            if values.has_zero_width(raw):
                col.zero_width += 1
            if unicodedata.normalize("NFC", raw) != raw:
                col.nfd += 1
            elif unicodedata.normalize("NFD", raw) != raw:
                col.nfc += 1
        # isprintable() is a single C call and is False for every control
        # character, so the regex only runs on cells that are actually odd.
        if not raw.isprintable() and CONTROL_RE.search(raw) is not None:
            col.control += 1
        if "\n" in raw or "\r" in raw:
            col.newline += 1
        first = raw[0]
        if first.isdigit():
            # Shape pre-test: a date always carries "-", "/" or two dots, so
            # plain amounts ("1,234.56") never reach the format regexes.
            if "-" in raw or "/" in raw or raw.count(".") >= 2:
                d = values.classify_date(raw)
                if d is not None:
                    col.date_patterns[d.pattern] = col.date_patterns.get(d.pattern, 0) + 1
            if len(raw) < 24:
                style = values.number_style(raw)
                if style is not None:
                    col.number_styles[style] = col.number_styles.get(style, 0) + 1
                    if style == "ambiguous_1000":
                        col.ambiguous += 1
        elif first.isalpha():
            if len(raw) >= 8 and raw[1].isalpha() and raw[2].isalpha():
                d = values.classify_date(raw)
                if d is not None:
                    col.date_patterns[d.pattern] = col.date_patterns.get(d.pattern, 0) + 1
        elif first in FIRST_CHARS_NUMERIC:
            d = values.classify_date(raw)
            if d is not None:
                col.date_patterns[d.pattern] = col.date_patterns.get(d.pattern, 0) + 1
            elif raw.count(",") > 1 or raw.count(".") > 1:
                col.ambiguous += 1
            else:
                style = values.number_style(raw)
                if style is not None:
                    col.number_styles[style] = col.number_styles.get(style, 0) + 1
                    if style == "ambiguous_1000":
                        col.ambiguous += 1
        if not col.distinct_overflow:
            stripped = raw.strip()
            if stripped:
                key2 = " ".join(stripped.split()).casefold() if " " in stripped else (
                    stripped.casefold()
                )
                bucket = col.distinct.get(key2)
                if bucket is None:
                    if len(col.distinct) >= MAX_DISTINCT_TRACKED:
                        col.distinct_overflow = True
                    else:
                        col.distinct[key2] = {stripped}
                elif stripped not in bucket:
                    bucket.add(stripped)


def _column_findings(
    columns: list[_ColumnState], rows: int, max_examples: int
) -> list[Finding]:
    out: list[Finding] = []
    for col in columns:
        where = "column " + str(col.index + 1) + " (" + col.name + ")"
        if col.whitespace:
            out.append(
                Finding(
                    "CS007",
                    RULES["CS007"].severity,
                    where + ": " + str(col.whitespace) + " value(s) with untrimmed whitespace",
                    count=col.whitespace,
                    column=col.index + 1,
                    column_name=col.name,
                )
            )
        if col.zero_width:
            out.append(
                Finding(
                    "CS008",
                    RULES["CS008"].severity,
                    where + ": " + str(col.zero_width) + " value(s) contain zero-width characters",
                    count=col.zero_width,
                    column=col.index + 1,
                    column_name=col.name,
                )
            )
        if col.control:
            out.append(
                Finding(
                    "CS009",
                    RULES["CS009"].severity,
                    where + ": " + str(col.control) + " value(s) contain control characters",
                    count=col.control,
                    column=col.index + 1,
                    column_name=col.name,
                )
            )
        dated = {k: v for k, v in col.date_patterns.items() if v}
        if len(dated) > 1:
            summary = ", ".join(k + " x" + str(v) for k, v in sorted(dated.items()))
            out.append(
                Finding(
                    "CS010",
                    RULES["CS010"].severity,
                    where + ": " + str(len(dated)) + " date formats (" + summary + ")",
                    count=sum(dated.values()),
                    column=col.index + 1,
                    column_name=col.name,
                    sample=summary,
                )
            )
        dot = sorted(k for k in col.number_styles if k in values.DOT_FAMILY)
        comma = sorted(k for k in col.number_styles if k in values.COMMA_FAMILY)
        ambig = col.number_styles.get("ambiguous_1000", 0) + col.ambiguous
        if (dot and comma) or ambig:
            bits = []
            if dot and comma:
                bits.append("conflicting decimal conventions (" + ", ".join(dot + comma) + ")")
            if ambig:
                bits.append(str(ambig) + " ambiguous value(s)")
            out.append(
                Finding(
                    "CS011",
                    RULES["CS011"].severity,
                    where + ": " + "; ".join(bits),
                    count=max(1, sum(col.number_styles.values())),
                    column=col.index + 1,
                    column_name=col.name,
                    sample=", ".join(dot + comma),
                )
            )
        if col.nfd and col.nfc:
            out.append(
                Finding(
                    "CS014",
                    RULES["CS014"].severity,
                    where
                    + ": "
                    + str(col.nfd)
                    + " NFD and "
                    + str(col.nfc)
                    + " NFC value(s) co-exist",
                    count=col.nfd + col.nfc,
                    column=col.index + 1,
                    column_name=col.name,
                )
            )
        collapsed = 0
        sample_pair = ""
        for _key, spellings in col.distinct.items():
            if len(spellings) > 1:
                collapsed += 1
                if not sample_pair:
                    sample_pair = " vs ".join(sorted(spellings)[:2])
        if collapsed:
            out.append(
                Finding(
                    "CS015",
                    RULES["CS015"].severity,
                    where + ": " + str(collapsed) + " value group(s) differ only by case/space",
                    count=collapsed,
                    column=col.index + 1,
                    column_name=col.name,
                    sample=sample_pair,
                )
            )
    return out


def rule_table() -> list[Rule]:
    """Every documented rule, sorted by code."""
    return [RULES[code] for code in sorted(RULES)]
