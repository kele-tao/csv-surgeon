"""Encoding and delimiter sniffing."""

from __future__ import annotations

from csv_surgeon.detect import detect_delimiter, detect_encoding, sniff_newlines


def test_utf8_bom_is_detected_and_reported_separately():
    guess = detect_encoding(b"\xef\xbb\xbfid,name\n1,a\n")
    assert guess.encoding == "utf-8"
    assert guess.has_bom
    assert guess.label == "utf-8 with BOM"


def test_utf16_le_bom():
    raw = b"\xff\xfe" + "id,name\r\n".encode("utf-16-le")
    guess = detect_encoding(raw)
    assert guess.encoding == "utf-16-le"
    assert guess.confidence == "certain"


def test_utf16_without_bom_uses_nul_parity():
    raw = "id;name;value\n1;alpha;2.5\n".encode("utf-16-le")
    guess = detect_encoding(raw)
    assert guess.encoding == "utf-16-le"
    assert guess.confidence == "likely"


def test_latin1_fallback():
    raw = "nom;ville\nCaf\xe9;Z\xfcrich\n".encode("latin-1")
    guess = detect_encoding(raw)
    assert guess.encoding in ("cp1252", "latin-1")
    assert not guess.has_bom


def test_empty_file_assumes_utf8():
    guess = detect_encoding(b"")
    assert guess.encoding == "utf-8"
    assert guess.confidence == "certain"


def test_delimiter_semicolon_beats_commas_inside_values():
    raw = b"a;b;c\n1;x,y;2\n3;z,w;4\n"
    guess = detect_delimiter(raw, "utf-8")
    assert guess.delimiter == ";"
    assert guess.confidence in ("certain", "likely")


def test_delimiter_tab():
    raw = b"a\tb\tc\n1\t2\t3\n4\t5\t6\n"
    assert detect_delimiter(raw, "utf-8").delimiter == "\t"


def test_single_column_defaults_to_comma():
    guess = detect_delimiter(b"one\ntwo\nthree\n", "utf-8")
    assert guess.delimiter == ","


def test_quoted_delimiters_do_not_count():
    raw = b"a,b\n\"x,y\",z\n\"p,q\",r\n"
    assert detect_delimiter(raw, "utf-8").delimiter == ","


def test_newline_counts():
    counts = sniff_newlines(b"a\r\nb\nc\nd\r\n")
    assert counts["crlf"] == 2
    assert counts["lf"] == 2
    assert counts["cr"] == 0


def test_newline_counts_lone_cr():
    counts = sniff_newlines(b"a\rb\rc\r")
    assert counts["cr"] == 3
    assert counts["crlf"] == 0
