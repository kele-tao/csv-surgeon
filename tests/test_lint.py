"""The lint rule engine: every rule code has at least one positive test."""

from __future__ import annotations

from csv_surgeon.lint import RULES, lint, rule_table


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


def test_rule_table_is_documented_and_stable():
    rules = rule_table()
    assert len(rules) == 15
    assert [r.code for r in rules] == [f"CS{i:03d}" for i in range(1, 16)]
    assert {r.severity for r in rules} <= {"error", "warn", "info"}


def test_clean_file_has_no_findings(clean_csv):
    report = lint(str(clean_csv))
    assert report.findings == []
    assert report.rows == 2
    assert report.columns == 3
    assert report.header == ["id", "name", "amount"]


def test_messy_example_triggers_the_promised_rules(messy_csv):
    report = lint(str(messy_csv))
    found = codes(report)
    # CS006 (non-LF / mixed line endings) depends on how git checked the file out:
    # a CRLF or autocrlf clone legitimately normalises it away, so it is asserted
    # separately below on a fixture written with mixed endings at runtime.
    expected_codes = (
        "CS001",
        "CS003",
        "CS004",
        "CS005",
        "CS007",
        "CS008",
        "CS010",
        "CS011",
        "CS012",
        "CS013",
    )
    for expected in expected_codes:
        assert expected in found, expected
    assert report.error_count >= 2


def test_mixed_line_endings_cs006(write_csv):
    path = write_csv("mixed.csv", b"a,b\r\n1,2\n3,4\r\n")
    assert "CS006" in codes(lint(str(path)))


def test_duplicate_header_cs001(write_csv):
    path = write_csv("dup.csv", b"a,b,a\n1,2,3\n")
    report = lint(str(path))
    finding = next(f for f in report.findings if f.code == "CS001")
    assert finding.count == 1
    assert "columns 1 and 3" in finding.examples[0][1]


def test_blank_header_cs002(write_csv):
    path = write_csv("blank.csv", b"a,,c\n1,2,3\n")
    assert "CS002" in codes(lint(str(path)))


def test_padded_header_cs002(write_csv):
    path = write_csv("pad.csv", b" id ,name\n1,a\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS002")
    assert "padded" in finding.examples[0][1]


def test_ragged_rows_cs004(write_csv):
    path = write_csv("ragged.csv", b"a,b,c\n1,2,3\n4,5\n6,7,8,9\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS004")
    assert finding.count == 2
    assert finding.line == 3


def test_bom_cs005(write_csv):
    path = write_csv("bom.csv", b"\xef\xbb\xbfa,b\n1,2\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS005")
    assert finding.severity == "info"
    assert "BOM" in finding.message


def test_crlf_cs006(write_csv):
    path = write_csv("crlf.csv", b"a,b\r\n1,2\r\n")
    assert "CS006" in codes(lint(str(path)))


def test_lf_only_has_no_cs006(clean_csv):
    assert "CS006" not in codes(lint(str(clean_csv)))


def test_whitespace_cs007(write_csv):
    path = write_csv("ws.csv", b"a,b\n1, x \n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS007")
    assert finding.column == 2


def test_zero_width_cs008(write_csv):
    path = write_csv("zw.csv", "a,b\n1,Umbrella\u200bCorp\n".encode())
    finding = next(f for f in lint(str(path)).findings if f.code == "CS008")
    assert finding.count == 1


def test_control_chars_cs009(write_csv):
    path = write_csv("ctl.csv", b"a,b\n1,x\x00y\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS009")
    assert finding.severity == "error"


def test_mixed_dates_cs010(write_csv):
    path = write_csv("dates.csv", b"d\n2024-01-02\n02/01/2024\nJan 2 2024\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS010")
    assert "ISO" in finding.message


def test_ambiguous_numbers_cs011(write_csv):
    rows = b'id,n\n1,"1.234"\n2,"1,234.56"\n3,"1.234,56"\n'
    path = write_csv("nums.csv", rows)
    finding = next(f for f in lint(str(path)).findings if f.code == "CS011")
    assert "conflicting decimal conventions" in finding.message


def test_duplicate_rows_cs012(write_csv):
    path = write_csv("dups.csv", b"a,b\n1,2\n3,4\n1,2\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS012")
    assert finding.count == 1
    assert finding.line == 4


def test_embedded_newline_cs013(write_csv):
    path = write_csv("multi.csv", b'a,b\n1,"x\ny"\n')
    finding = next(f for f in lint(str(path)).findings if f.code == "CS013")
    assert "spans lines 2-3" in finding.examples[0][1]


def test_nfc_nfd_cs014(write_csv):
    path = write_csv("nfd.csv", "name\ncaf\u00e9\ncafe\u0301\n".encode())
    assert "CS014" in codes(lint(str(path)))


def test_near_duplicate_values_cs015(write_csv):
    path = write_csv("near.csv", b"name\nAcme Corp\nacme corp\nACME  CORP\n")
    finding = next(f for f in lint(str(path)).findings if f.code == "CS015")
    assert finding.count == 1


def test_findings_are_sorted_by_severity_then_code(messy_csv):
    report = lint(str(messy_csv))
    order = {"error": 0, "warn": 1, "info": 2}
    keys = [(order[f.severity], f.code) for f in report.findings]
    assert keys == sorted(keys)


def test_json_round_trip_is_serialisable(messy_csv):
    import json

    payload = lint(str(messy_csv)).as_dict()
    text = json.dumps(payload)
    assert json.loads(text)["counts"]["error"] >= 2


def test_empty_file_is_reported_not_crashed(write_csv):
    path = write_csv("none.csv", b"")
    report = lint(str(path))
    assert report.rows == 0
    assert "file is empty" in report.notes


def test_header_only_file(write_csv):
    path = write_csv("head.csv", b"a,b,c\n")
    report = lint(str(path))
    assert report.rows == 0
    assert report.columns == 3


def test_examples_are_capped(write_csv):
    rows = b"a,b\n" + b"1\n" * 40
    path = write_csv("many.csv", rows)
    report = lint(str(path))
    finding = next(f for f in report.findings if f.code == "CS004")
    assert finding.count == 40
    assert len(finding.examples) == 5


def test_large_input_is_streamed(write_csv):
    path = write_csv("big.csv", b"a,b,c\n" + b"1,2,3\n" * 50000)
    report = lint(str(path))
    assert report.rows == 50000
    assert RULES["CS004"].severity == "error"
