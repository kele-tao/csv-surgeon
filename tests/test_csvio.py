"""The streaming parser: record boundaries, quoting and physical lines."""

from __future__ import annotations

from csv_surgeon.csvio import inspect, iter_records, parse_record, sample_records, strip_terminator


def test_strip_terminator():
    assert strip_terminator("a,b\r\n") == "a,b"
    assert strip_terminator("a,b\n") == "a,b"
    assert strip_terminator("a,b\r") == "a,b"
    assert strip_terminator("a,b") == "a,b"


def test_plain_record():
    fields, problems, unterminated = parse_record("1,2,3", ",")
    assert fields == ["1", "2", "3"]
    assert problems == []
    assert not unterminated


def test_quoted_field_with_delimiter_and_escaped_quote():
    fields, problems, _ = parse_record('"a,b","say ""hi"""', ",")
    assert fields == ["a,b", 'say "hi"']
    assert problems == []


def test_quoted_embedded_newline():
    fields, _, unterminated = parse_record('"line1\nline2",x', ",")
    assert fields == ["line1\nline2", "x"]
    assert not unterminated


def test_stray_quote_is_reported():
    _, problems, _ = parse_record('ab"cd,e', ",")
    assert len(problems) == 1
    assert problems[0].code == "CS003"
    assert problems[0].column == 0


def test_text_after_closing_quote_is_reported():
    _, problems, _ = parse_record('"ab"cd,e', ",")
    assert problems and problems[0].code == "CS003"


def test_unterminated_quote():
    _, _, unterminated = parse_record('"never closed,x', ",")
    assert unterminated


def test_iter_records_tracks_physical_lines(write_csv):
    path = write_csv("multi.csv", b'id,note\n1,"a\nb"\n2,plain\n')
    info = inspect(str(path))
    records = list(iter_records(info))
    assert len(records) == 3
    assert records[1].line_no == 2
    assert records[1].end_line == 3
    assert records[1].multiline
    assert records[1].fields == ["1", "a\nb"]
    assert records[2].line_no == 4


def test_bom_is_stripped_from_first_field(write_csv):
    path = write_csv("bom.csv", b"\xef\xbb\xbfid,name\n1,a\n")
    info = inspect(str(path))
    records = list(iter_records(info))
    assert records[0].fields == ["id", "name"]


def test_empty_file_yields_no_records(write_csv):
    path = write_csv("empty.csv", b"")
    assert list(iter_records(inspect(str(path)))) == []


def test_utf16_file_is_read(write_csv):
    path = write_csv("u16.csv", b"\xff\xfe" + "id;name\r\n1;caf\u00e9\r\n".encode("utf-16-le"))
    info = inspect(str(path))
    assert info.encoding == "utf-16-le"
    records = list(iter_records(info))
    assert records[0].fields == ["id", "name"]
    assert records[1].fields == ["1", "caf\u00e9"]


def test_cjk_and_emoji_round_trip(write_csv):
    path = write_csv("cjk.csv", "id,name,mood\n1,\u5f20\u4e09,\U0001f600\n".encode())
    records = list(iter_records(inspect(str(path))))
    assert records[1].fields == ["1", "\u5f20\u4e09", "\U0001f600"]


def test_sample_records_limit(clean_csv):
    info = inspect(str(clean_csv))
    assert len(sample_records(info, 2)) == 2
    assert sample_records(info, 0) == []


def test_tab_delimiter(write_csv):
    path = write_csv("tab.csv", b"a\tb\n1\t2\n3\t4\n")
    info = inspect(str(path))
    assert info.delimiter == "\t"
    assert list(iter_records(info))[1].fields == ["1", "2"]
