"""Per-column profiling."""

from __future__ import annotations

from csv_surgeon.profile import profile


def test_basic_profile(clean_csv):
    report = profile(str(clean_csv))
    assert report.rows == 2
    assert [c.name for c in report.columns] == ["id", "name", "amount"]
    assert report.columns[0].dominant_type == "int"
    assert report.columns[1].dominant_type == "text"
    assert report.columns[2].types == {"float": 1, "int": 1}


def test_top_values_are_ordered_by_frequency(write_csv):
    path = write_csv("t.csv", b"v\nb\na\nb\nb\n")
    col = profile(str(path)).columns[0]
    assert col.top(2) == [("b", 3), ("a", 1)]
    assert col.distinct == 2


def test_type_mix_is_reported(write_csv):
    path = write_csv("mix.csv", b"v\n1\ntwo\n3\n")
    col = profile(str(path)).columns[0]
    assert col.mixed
    assert col.types["int"] == 2
    assert col.types["text"] == 1


def test_null_patterns_are_classified(write_csv):
    path = write_csv("nulls.csv", b"v\n\nNULL\nN/A\n-\nreal\n")
    col = profile(str(path)).columns[0]
    assert col.nulls["empty"] == 1
    assert col.nulls["NULL/None"] == 1
    assert col.nulls["N/A"] == 1
    assert col.nulls["dash"] == 1


def test_longest_value_and_range(write_csv):
    path = write_csv("long.csv", b"v\nshort\nmuchlongervalue\nmid\n")
    col = profile(str(path)).columns[0]
    assert col.longest[0] == 15
    assert col.min_value == "mid"
    assert col.max_value == "short"


def test_numeric_range_is_numeric_not_lexical(write_csv):
    path = write_csv("num.csv", b"v\n9\n10\n2\n")
    col = profile(str(path)).columns[0]
    assert col.min_value == "2"
    assert col.max_value == "10"


def test_date_range_is_chronological(write_csv):
    path = write_csv("dates.csv", b"d\n2024-03-01\n2024-01-05\n2024-12-31\n")
    col = profile(str(path)).columns[0]
    assert col.min_value == "2024-01-05"
    assert col.max_value == "2024-12-31"


def test_near_duplicate_groups(write_csv):
    path = write_csv("near.csv", b"name\nAcme Corp\nacme corp\nAcme  Corp\nGlobex\n")
    col = profile(str(path)).columns[0]
    assert col.near_duplicates
    key, spellings = col.near_duplicates[0]
    assert "acme corp" in key
    assert len(spellings) == 3


def test_whitespace_and_invisible_counters(write_csv):
    path = write_csv("ws.csv", "v\n x \n\u200bhidden\n".encode())
    col = profile(str(path)).columns[0]
    assert col.whitespace == 1
    assert col.invisible == 1


def test_distinct_cap_is_a_lower_bound(write_csv):
    rows = b"v\n" + b"".join(b"v%d\n" % i for i in range(30))
    path = write_csv("many.csv", rows)
    report = profile(str(path), max_distinct=10)
    assert report.columns[0].distinct_capped
    assert report.notes


def test_json_serialisable(messy_csv):
    import json

    payload = profile(str(messy_csv)).as_dict()
    assert json.loads(json.dumps(payload))["rows"] == 8


def test_empty_file(write_csv):
    path = write_csv("empty.csv", b"")
    report = profile(str(path))
    assert report.rows == 0
    assert report.columns == []
