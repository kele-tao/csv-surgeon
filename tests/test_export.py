"""JSON and SQL export."""

from __future__ import annotations

import io
import json

from csv_surgeon.csvio import inspect
from csv_surgeon.export import infer_types, json_key_names, sql_column_names, write_json, write_sql


def _info(path):
    return inspect(str(path))


def test_json_array_of_objects(clean_csv):
    buf = io.StringIO()
    rows = write_json(_info(clean_csv), buf)
    payload = json.loads(buf.getvalue())
    assert rows == 2
    assert payload[0] == {"id": "1", "name": "Alice", "amount": "10.5"}


def test_json_pretty_print(clean_csv):
    buf = io.StringIO()
    write_json(_info(clean_csv), buf, indent=2)
    text = buf.getvalue()
    assert text.startswith("[\n")
    assert json.loads(text)[1]["name"] == "Bob"


def test_json_keeps_duplicate_keys_apart(write_csv):
    path = write_csv("dup.csv", b"a,a\n1,2\n")
    buf = io.StringIO()
    write_json(_info(path), buf)
    assert json.loads(buf.getvalue()) == [{"a": "1", "a_2": "2"}]


def test_json_empty_file_is_empty_array(write_csv):
    path = write_csv("empty.csv", b"")
    buf = io.StringIO()
    assert write_json(_info(path), buf) == 0
    assert json.loads(buf.getvalue()) == []


def test_json_preserves_unicode(write_csv):
    path = write_csv("u.csv", "id,name\n1,\u5f20\u4e09\U0001f600\n".encode())
    buf = io.StringIO()
    write_json(_info(path), buf)
    assert "\u5f20\u4e09" in buf.getvalue()


def test_sql_identifiers_are_safe():
    assert sql_column_names(["order id", "2nd", "", "a-b"]) == [
        "order_id",
        "c_2nd",
        "column_3",
        "a_b",
    ]


def test_sql_identifiers_are_unique():
    assert sql_column_names(["a", "a", "a"]) == ["a", "a_2", "a_3"]


def test_json_key_names():
    assert json_key_names(["a", " ", "a"]) == ["a", "column_2", "a_2"]


def test_create_table_types(clean_csv):
    types = infer_types(_info(clean_csv))
    assert types.sql_types == ["INTEGER", "TEXT", "DOUBLE PRECISION"]
    ddl = types.create_table("t")
    assert "id INTEGER NOT NULL" in ddl
    assert ddl.startswith("CREATE TABLE t (")


def test_nulls_make_a_column_nullable(write_csv):
    path = write_csv("n.csv", b"a\n1\n\n3\n")
    types = infer_types(_info(path))
    assert types.nullable == [True]
    assert "NOT NULL" not in types.create_table("t")


def test_mixed_column_becomes_text(write_csv):
    path = write_csv("m.csv", b"a\n1\ntwo\n")
    assert infer_types(_info(path)).sql_types == ["TEXT"]


def test_date_and_timestamp_types(write_csv):
    path = write_csv("d.csv", b"d,t\n2024-01-02,2024-01-02T03:04:05\n")
    assert infer_types(_info(path)).sql_types == ["DATE", "TIMESTAMP"]


def test_inserts_are_batched_and_escaped(write_csv):
    path = write_csv("q.csv", b"a,b\n1,it's\n")
    buf = io.StringIO()
    rows = write_sql(_info(path), buf, table="orders", batch_size=1)
    text = buf.getvalue()
    assert rows == 1
    assert "CREATE TABLE orders" in text
    assert "INSERT INTO orders (a, b) VALUES" in text
    assert "'it''s'" in text


def test_sql_batching_splits_statements(write_csv):
    path = write_csv("b.csv", b"a\n1\n2\n3\n")
    buf = io.StringIO()
    write_sql(_info(path), buf, table="t", batch_size=2)
    assert buf.getvalue().count("INSERT INTO") == 2


def test_sql_no_create_flag(write_csv):
    path = write_csv("b.csv", b"a\n1\n")
    buf = io.StringIO()
    write_sql(_info(path), buf, table="t", include_create=False)
    assert "CREATE TABLE" not in buf.getvalue()


def test_sqlite_dialect_spells_double_precision(clean_csv):
    buf = io.StringIO()
    write_sql(_info(clean_csv), buf, table="t", dialect="sqlite")
    assert "REAL" in buf.getvalue()
    assert "DOUBLE PRECISION" not in buf.getvalue()


def test_numbers_are_not_quoted(write_csv):
    path = write_csv("n.csv", b"a\n1\n2\n")
    buf = io.StringIO()
    write_sql(_info(path), buf, table="t")
    assert "(1)" in buf.getvalue()
