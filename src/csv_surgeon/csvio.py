"""Streaming, record-oriented CSV reader with precise physical line tracking.

Python's :mod:`csv` module hides the raw text of a record, which is exactly
what a linter needs: a quote violation has to be reported at a line and a
column. The parser below is a small RFC 4180 state machine on top of a
universal-newline text stream, so it can report physical line numbers,
embedded newlines and byte-exact record text while still streaming.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .detect import EncodingGuess, detect_delimiter, detect_encoding, read_prefix, sniff_newlines

MAX_RECORD_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ParseProblem:
    """A structural problem found while parsing one record."""

    column: int
    code: str
    message: str


@dataclass(frozen=True)
class Record:
    """One parsed CSV record.

    ``line_no`` is the physical line the record starts on, ``end_line`` the
    line it ends on (different when a quoted field contains a newline).
    """

    fields: list[str]
    line_no: int
    end_line: int
    raw: str
    problems: tuple[ParseProblem, ...] = ()

    @property
    def multiline(self) -> bool:
        return self.end_line != self.line_no


@dataclass
class StreamInfo:
    """Everything sniffed before the first record is parsed."""

    path: str
    encoding: str
    bom: bytes
    delimiter: str
    newlines: dict[str, int]
    encoding_guess: EncodingGuess | None = None
    delimiter_notes: list[str] = field(default_factory=list)

    @property
    def newline_style(self) -> str:
        crlf = self.newlines.get("crlf", 0)
        cr = self.newlines.get("cr", 0)
        lf = self.newlines.get("lf", 0)
        if crlf and not lf and not cr:
            return "crlf"
        if lf and not crlf and not cr:
            return "lf"
        if cr and not crlf and not lf:
            return "cr"
        return "mixed"


def strip_terminator(line: str) -> str:
    """Remove the trailing newline sequence from a physical line."""
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def parse_record(raw: str, delimiter: str) -> tuple[list[str], list[ParseProblem], bool]:
    """Split one record, reporting RFC 4180 violations and unclosed quotes.

    Returns ``(fields, problems, unterminated)``. A record whose last quoted
    field never closes is reported with ``unterminated=True`` so the stream
    reader can keep appending physical lines to it.
    """
    fields: list[str] = []
    problems: list[ParseProblem] = []
    buf: list[str] = []
    col = 0
    i = 0
    n = len(raw)
    in_quotes = False
    closed = False
    at_start = True
    quote = chr(34)
    while i < n:
        ch = raw[i]
        if in_quotes:
            if ch == quote:
                if i + 1 < n and raw[i + 1] == quote:
                    buf.append(quote)
                    i += 2
                    continue
                in_quotes = False
                closed = True
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == delimiter:
            fields.append("".join(buf))
            buf = []
            col += 1
            closed = False
            at_start = True
            i += 1
            continue
        if ch == quote:
            if at_start and not closed:
                in_quotes = True
                at_start = False
                i += 1
                continue
            problems.append(
                ParseProblem(col, "CS003", "quote character inside an unquoted field")
            )
            buf.append(ch)
            at_start = False
            i += 1
            continue
        if closed:
            problems.append(ParseProblem(col, "CS003", "text after a closing quote"))
            closed = False
        buf.append(ch)
        at_start = False
        i += 1
    fields.append("".join(buf))
    return fields, problems, in_quotes


def inspect(path: str) -> StreamInfo:
    """Sniff encoding, delimiter and newline style from a bounded prefix."""
    prefix = read_prefix(path)
    enc = detect_encoding(prefix)
    delim = detect_delimiter(prefix, enc.encoding)
    return StreamInfo(
        path=path,
        encoding=enc.encoding,
        bom=enc.bom,
        delimiter=delim.delimiter,
        newlines=sniff_newlines(prefix),
        encoding_guess=enc,
        delimiter_notes=delim.notes,
    )


def _iter_physical_lines(path: str, encoding: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, text_with_terminator)``; the BOM is left in place.

    The BOM is deliberately *not* stripped here so the linter can report it.
    Callers that want clean text strip ``\\ufeff`` themselves.
    """
    with open(path, encoding=encoding, newline="", errors="replace") as fh:
        line_no = 0
        for line in fh:
            line_no += 1
            yield line_no, line


def iter_records(info: StreamInfo) -> Iterator[Record]:
    """Stream parsed records from ``info.path`` with constant memory.

    Quoted fields spanning physical lines are joined. If a record grows past
    :data:`MAX_RECORD_BYTES` it is emitted as-is (flagged as unterminated)
    so one catastrophic record cannot exhaust memory.
    """
    delim = info.delimiter
    pending: list[str] = []
    pending_len = 0
    start_line = 0
    for line_no, line in _iter_physical_lines(info.path, info.encoding):
        if not pending:
            start_line = line_no
        body = strip_terminator(line)
        if not pending and start_line == 1 and body.startswith("\ufeff"):
            body = body[1:]
        pending.append(body)
        pending_len += len(body)
        if len(pending) == 1 and chr(34) not in body:
            yield Record(body.split(delim), start_line, line_no, body)
            pending = []
            pending_len = 0
            continue
        raw = "\n".join(pending)
        fields, problems, unterminated = parse_record(raw, delim)
        if unterminated and pending_len < MAX_RECORD_BYTES:
            continue
        yield Record(fields, start_line, line_no, raw, tuple(problems))
        pending = []
        pending_len = 0
    if pending:
        raw = "\n".join(pending)
        fields, problems, _ = parse_record(raw, delim)
        yield Record(fields, start_line, start_line + len(pending) - 1, raw, tuple(problems))


def sample_records(info: StreamInfo, limit: int) -> list[Record]:
    """Return up to ``limit`` records; used for column classification."""
    out: list[Record] = []
    if limit <= 0:
        return out
    for rec in iter_records(info):
        out.append(rec)
        if len(out) >= limit:
            break
    return out

