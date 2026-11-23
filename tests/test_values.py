"""Value classifiers: numbers, currencies, dates and invisible characters."""

from __future__ import annotations

import datetime

from csv_surgeon import values


def test_plain_integer_and_float():
    assert values.classify_number("42").kind == "int"
    assert values.classify_number("-3.5").value == -3.5
    assert values.classify_number("1e3").value == 1000.0


def test_grouped_thousands_and_decimal():
    info = values.classify_number("1,234.56")
    assert info is not None and info.kind == "float"
    assert info.value == 1234.56


def test_european_grouping():
    info = values.classify_number("1.234,56")
    assert info is not None and info.value == 1234.56


def test_currency_symbols():
    assert values.classify_number("$1,234.50").kind == "currency"
    assert values.classify_number("\u20ac99").value == 99.0


def test_percent_and_accounting_negative():
    assert values.classify_number("12%").kind == "percent"
    assert values.classify_number("(42)").value == -42.0


def test_non_numbers_return_none():
    assert values.classify_number("abc") is None
    assert values.classify_number("") is None
    assert values.classify_number("2024-01-02") is None


def test_ambiguous_single_separator():
    assert values.ambiguous_number("1.234") is not None
    assert values.ambiguous_number("1,234") is not None
    assert values.ambiguous_number("1,234.56") is None
    assert values.ambiguous_number("12") is None


def test_number_style_classification():
    assert values.number_style("1.23") == "dot_decimal"
    assert values.number_style("1,23") == "comma_decimal"
    assert values.number_style("1,234.56") == "comma_thousands"
    assert values.number_style("1.234,56") == "dot_thousands"
    assert values.number_style("1.234") == "ambiguous_1000"
    assert values.number_style("hello") is None


def test_date_formats():
    assert values.classify_date("2024-01-02").pattern == "ISO"
    assert values.classify_date("02/01/2024").pattern == "DMY_SLASH_OR_MDY"
    assert values.classify_date("Jan 2 2024").value == datetime.date(2024, 1, 2)
    assert values.classify_date("2 January 2024").value == datetime.date(2024, 1, 2)
    assert values.classify_date("2 Jan 2024").value == datetime.date(2024, 1, 2)
    assert values.classify_date("31/01/2024").pattern == "DMY_SLASH"
    assert values.classify_date("01/31/2024").pattern == "DMY_SLASH"


def test_bad_dates_are_rejected():
    assert values.classify_date("2024-02-31") is None
    assert values.classify_date("not a date") is None


def test_datetime():
    info = values.classify_date("2024-01-02T03:04:05Z")
    assert info is not None and info.has_time


def test_zero_width_detection_and_strip():
    assert values.has_zero_width("Umbrella\u200bCorp")
    assert not values.has_zero_width("Umbrella Corp")
    assert values.strip_invisible("a\u200bb\ufeff") == "ab"


def test_control_characters_exclude_tab_and_newline():
    assert values.find_control_chars("a\tb") == []
    assert values.find_control_chars("a\x00b") == ["\x00"]


def test_normalise_value():
    assert values.normalise_value("  Acme   Corp ") == "acme corp"


def test_null_like():
    assert values.is_null_like("")
    assert values.is_null_like("N/A")
    assert not values.is_null_like("0")
