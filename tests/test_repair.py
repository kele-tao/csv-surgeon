"""The repair pipeline and its change log."""

from __future__ import annotations

import json

import pytest

from csv_surgeon.repair import ALL_FIXES, FixError, parse_fixes, repair


def test_parse_fixes_all():
    assert parse_fixes("all") == ALL_FIXES


def test_parse_fixes_subset_is_ordered():
    assert parse_fixes("dedupe,trim") == ("trim", "dedupe")


def test_parse_fixes_rejects_unknown():
    with pytest.raises(FixError, match="unknown repair"):
        parse_fixes("trim,magic")


def test_parse_fixes_rejects_empty():
    with pytest.raises(FixError):
        parse_fixes(" , ")


def test_dry_run_writes_nothing(write_csv):
    path = write_csv("in.csv", b"a , b\r\n1,2\r\n")
    report = repair(str(path), ("trim", "newline"))
    assert report.out_path is None
    assert report.rows_out == report.rows_in


def test_bom_is_stripped(write_csv, tmp_path):
    path = write_csv("bom.csv", b"\xef\xbb\xbfa,b\r\n1,2\r\n")
    out = tmp_path / "clean.csv"
    repair(str(path), ("bom", "newline"), str(out))
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")


def test_crlf_becomes_lf(write_csv, tmp_path):
    path = write_csv("crlf.csv", b"a,b\r\n1,2\r\n")
    out = tmp_path / "clean.csv"
    repair(str(path), ("newline",), str(out))
    assert b"\r" not in out.read_bytes()


def test_header_is_deduplicated_and_named(write_csv, tmp_path):
    path = write_csv("hdr.csv", b"a,,a\r\n1,2,3\r\n")
    out = tmp_path / "clean.csv"
    repair(str(path), ("header",), str(out))
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert header == "a,column_2,a_2"


def test_trim_removes_padding(write_csv, tmp_path):
    path = write_csv("ws.csv", b"a,b\r\n 1 , x \r\n")
    out = tmp_path / "clean.csv"
    repair(str(path), ("trim",), str(out))
    assert "1,x" in out.read_text(encoding="utf-8")


def test_zero_width_is_removed(write_csv, tmp_path):
    path = write_csv("zw.csv", "a,b\r\n1,Umb\u200brella\r\n".encode())
    out = tmp_path / "clean.csv"
    repair(str(path), ("zero-width",), str(out))
    assert "Umbrella" in out.read_text(encoding="utf-8")


def test_control_chars_are_removed(write_csv, tmp_path):
    path = write_csv("ctl.csv", b"a,b\r\n1,x\x00y\r\n")
    out = tmp_path / "clean.csv"
    repair(str(path), ("control",), str(out))
    assert b"\x00" not in out.read_bytes()


def test_dedupe_removes_exact_duplicate_rows(write_csv, tmp_path):
    path = write_csv("dup.csv", b"a,b\r\n1,2\r\n1,2\r\n3,4\r\n")
    out = tmp_path / "clean.csv"
    report = repair(str(path), ("dedupe",), str(out))
    assert report.counts["dedupe"] == 1
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_blank_rows_are_dropped(write_csv, tmp_path):
    path = write_csv("blank.csv", b"a,b\r\n1,2\r\n,\r\n3,4\r\n")
    out = tmp_path / "clean.csv"
    report = repair(str(path), ("blank-rows",), str(out))
    assert report.counts["blank-rows"] == 1
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_quotes_are_canonicalised(write_csv, tmp_path):
    path = write_csv("q.csv", b'a,b\r\n1,"say ""hi"""\r\n')
    out = tmp_path / "clean.csv"
    report = repair(str(path), ("quotes",), str(out))
    assert report.counts.get("quotes", 0) == 0
    assert '1,"say ""hi"""' in out.read_text(encoding="utf-8")


def test_repair_is_idempotent(write_csv, tmp_path):
    path = write_csv("idem.csv", b"\xef\xbb\xbfa ,a\r\n 1 , 1 \r\n1,1\r\n")
    first = tmp_path / "one.csv"
    second = tmp_path / "two.csv"
    repair(str(path), ALL_FIXES, str(first))
    report = repair(str(first), ALL_FIXES, str(second))
    assert first.read_bytes() == second.read_bytes()
    assert report.counts.get("trim", 0) == 0


def test_change_log_is_machine_readable(write_csv, tmp_path):
    from csv_surgeon.repair import write_change_log

    path = write_csv("log.csv", b"a,b\r\n 1 ,x\r\n")
    report = repair(str(path), ("trim",))
    log = tmp_path / "log.json"
    write_change_log(report, str(log))
    payload = json.loads(log.read_text(encoding="utf-8"))
    assert payload["counts"]["trim"] == 1
    assert payload["changes"][0]["action"] == "trim"
    assert payload["output"] is None


def test_repair_requires_a_fix(clean_csv):
    with pytest.raises(FixError):
        repair(str(clean_csv), ())


def test_messy_example_is_repaired(messy_csv, tmp_path):
    out = tmp_path / "clean.csv"
    report = repair(str(messy_csv), ALL_FIXES, str(out))
    assert report.counts["bom"] == 1
    assert report.counts["dedupe"] == 1
    text = out.read_text(encoding="utf-8")
    assert "UmbrellaCorp" in text
    assert not text.startswith("\ufeff")
    assert "status_2" in text
    assert report.rows_out == report.rows_in - 1
