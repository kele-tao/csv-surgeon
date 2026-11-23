"""Encoding, delimiter, newline and control-character sniffing.

Everything here works on a bounded prefix of the file (default 256 KiB) so a
sniff never loads a whole export into memory.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

PREFIX_BYTES = 256 * 1024

BOM_UTF8 = codecs.BOM_UTF8
BOM_UTF16_LE = codecs.BOM_UTF16_LE
BOM_UTF16_BE = codecs.BOM_UTF16_BE

CANDIDATE_DELIMITERS = (",", ";", "\t", "|")


@dataclass(frozen=True)
class EncodingGuess:
    """Result of :func:`detect_encoding`."""

    encoding: str
    bom: bytes
    confidence: str
    notes: list[str]

    @property
    def has_bom(self) -> bool:
        return bool(self.bom)

    @property
    def label(self) -> str:
        """Human label such as ``utf-8 with BOM``."""
        base = self.encoding
        return base + " with BOM" if self.bom else base


@dataclass(frozen=True)
class DelimiterGuess:
    """Result of :func:`detect_delimiter`."""

    delimiter: str
    confidence: str
    field_counts: list[int]
    notes: list[str]


def _decode(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace")


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = 0
    for ch in text:
        if ch in "\r\n\t" or " " <= ch <= "~" or ch.isprintable():
            good += 1
    return good / len(text)


def _utf16_score(raw: bytes) -> float:
    """Fraction of NUL bytes in the expected parity positions for UTF-16."""
    if len(raw) < 4:
        return 0.0
    even = raw[0::2]
    odd = raw[1::2]
    even_nul = even.count(0) / len(even)
    odd_nul = odd.count(0) / len(odd)
    return max(even_nul, odd_nul)


def detect_encoding(raw: bytes) -> EncodingGuess:
    """Guess the encoding of a CSV prefix, handling UTF-8/16 BOMs and Latin-1.

    The order matters: a BOM is authoritative, then strict UTF-8 decode, then
    the UTF-16 NUL-parity heuristic, and only then a windows-1252 / latin-1
    fallback. ``utf-8-sig`` never appears here because callers strip the BOM
    themselves - keeping the BOM separate lets the repairable CS005 rule fire.
    """
    if raw.startswith(BOM_UTF8):
        return EncodingGuess("utf-8", BOM_UTF8, "certain", ["UTF-8 byte order mark present"])
    if raw.startswith(BOM_UTF16_LE):
        return EncodingGuess(
            "utf-16-le", BOM_UTF16_LE, "certain", ["UTF-16 little-endian byte order mark present"]
        )
    if raw.startswith(BOM_UTF16_BE):
        return EncodingGuess(
            "utf-16-be", BOM_UTF16_BE, "certain", ["UTF-16 big-endian byte order mark present"]
        )
    if not raw:
        return EncodingGuess("utf-8", b"", "certain", ["empty file, UTF-8 assumed"])
    notes: list[str] = []
    # UTF-16 has NUL bytes in every second position; UTF-8 never does. Checking
    # this first avoids mis-reporting UTF-16 as ASCII-with-NULs (a valid decode).
    if _utf16_score(raw) > 0.3:
        le = _decode(raw, "utf-16-le")
        be = _decode(raw, "utf-16-be")
        best = "utf-16-le" if _printable_ratio(le) >= _printable_ratio(be) else "utf-16-be"
        return EncodingGuess(best, b"", "likely", ["high NUL parity, UTF-16 without BOM"])
    try:
        raw.decode("utf-8")
        return EncodingGuess("utf-8", b"", "likely", ["decodes cleanly as UTF-8"])
    except UnicodeDecodeError as exc:
        notes = ["UTF-8 decode failed at byte " + str(exc.start)]
    if _utf16_score(raw) > 0.3:
        le = _decode(raw, "utf-16-le")
        be = _decode(raw, "utf-16-be")
        best = "utf-16-le" if _printable_ratio(le) >= _printable_ratio(be) else "utf-16-be"
        return EncodingGuess(best, b"", "likely", [*notes, "high NUL parity, UTF-16 without BOM"])
    latin = _decode(raw, "cp1252")
    if _printable_ratio(latin) >= 0.995:
        return EncodingGuess(
            "cp1252",
            b"",
            "guessed",
            [*notes, "bytes are valid windows-1252, file is probably Latin-1"],
        )
    return EncodingGuess(
        "latin-1",
        b"",
        "guessed",
        [*notes, "falling back to latin-1, text will not round-trip cleanly"],
    )


def _count_fields(lines: list[str], delimiter: str) -> list[int]:
    """Count unquoted delimiter occurrences per line.

    A cheap approximation is enough for sniffing: quotes are toggled so that
    delimiters inside a quoted field are ignored.
    """
    counts: list[int] = []
    quote = chr(34)
    for line in lines:
        in_quotes = False
        fields = 1
        for ch in line:
            if ch == quote:
                in_quotes = not in_quotes
            elif ch == delimiter and not in_quotes:
                fields += 1
        counts.append(fields)
    return counts


def _consistency(counts: list[int]) -> tuple[float, int]:
    """Return (share of lines agreeing with the modal field count, modal count)."""
    counts = [c for c in counts if c > 1 or len(counts) == 1]
    if not counts:
        return 0.0, 0
    tally: dict[int, int] = {}
    for c in counts:
        tally[c] = tally.get(c, 0) + 1
    modal, hits = max(tally.items(), key=lambda kv: (kv[1], kv[0]))
    return hits / len(counts), modal


def detect_delimiter(raw: bytes, encoding: str, sample_lines: int = 40) -> DelimiterGuess:
    """Pick the delimiter that yields the most consistent field counts."""
    text = _decode(raw[:PREFIX_BYTES], encoding)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()][:sample_lines]
    if not lines:
        return DelimiterGuess(",", "certain", [], ["empty file, comma assumed"])
    header_counts: dict[str, int] = {
        delim: _count_fields(lines[:1], delim)[0] for delim in CANDIDATE_DELIMITERS
    }
    best: tuple[float, float, int, str] | None = None
    for delim in CANDIDATE_DELIMITERS:
        # A real CSV delimiter also appears in the header row. Requiring that
        # stops a file of numbers ("1,234.56") from being read as two columns.
        if header_counts.get(delim, 1) < 2:
            continue
        counts = _count_fields(lines, delim)
        share, modal = _consistency(counts)
        if modal < 2:
            continue
        # Weight consistency by the field count it produces: a delimiter that
        # yields 6 stable fields explains the file better than one that yields
        # 2 because values happen to contain commas.
        score = share * modal
        if header_counts.get(delim) == modal:
            score *= 1.1
        if best is None or score > best[0]:
            best = (score, share, modal, delim)
    if best is None:
        return DelimiterGuess(",", "certain", [1] * len(lines), ["single column, comma assumed"])
    _, share, modal, delim = best
    name = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}[delim]
    confidence = "certain" if share >= 0.98 else "likely" if share >= 0.8 else "guessed"
    return DelimiterGuess(delim, confidence, [modal], [name + " looks consistent across sample"])


def sniff_newlines(raw: bytes) -> dict[str, int]:
    """Count CRLF, lone CR and lone LF sequences in a byte prefix."""
    crlf = raw.count(b"\r\n")
    total_cr = raw.count(b"\r")
    total_lf = raw.count(b"\n")
    return {"crlf": crlf, "cr": total_cr - crlf, "lf": total_lf - crlf}


def read_prefix(path: str, limit: int = PREFIX_BYTES) -> bytes:
    """Read at most ``limit`` bytes from the start of a file."""
    with open(path, "rb") as fh:
        return fh.read(limit)
