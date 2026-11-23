"""JSON and SQL export built on the same streaming reader.

Type inference for CREATE TABLE is column-wide: every non-null value is
classified first, then the narrowest type that holds all of them is chosen.
Nothing is truncated and nothing is invented - a column that is 99 percent
integers and 1 percent text becomes TEXT.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import IO

from . import values
from .csvio import StreamInfo, inspect, iter_records

SQL_IDENT_LIMIT = 63


def _sql_ident(name: str, fallback: str) -> str:
    clean = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name.strip())
    clean = clean.strip("_")
    if not clean:
        clean = fallback
    if clean[0].isdigit():
        clean = "c_" + clean
    return clean[:SQL_IDENT_LIMIT]


def sql_column_names(header: list[str]) -> list[str]:
    """Turn CSV headers into unique, safe SQL identifiers."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, name in enumerate(header):
        base = _sql_ident(values.strip_invisible(name), "column_" + str(i + 1))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else base + "_" + str(n + 1))
    return out


def json_key_names(header: list[str]) -> list[str]:
    """Header cells to JSON keys: trimmed, invisible characters removed, unique."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, name in enumerate(header):
        base = values.strip_invisible(name).strip() or "column_" + str(i + 1)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else base + "_" + str(n + 1))
    return out


@dataclass
class ColumnTypes:
    """Column-wide type decision used by ``CREATE TABLE``."""

    header: list[str]
    names: list[str]
    sql_types: list[str]
    nullable: list[bool]

    def create_table(self, table: str) -> str:
        lines = ["CREATE TABLE " + table + " ("]
        body = []
        pairs = zip(self.names, self.sql_types, self.nullable, strict=False)
        for name, sql_type, nullable in pairs:
            body.append("    " + name + " " + sql_type + ("" if nullable else " NOT NULL"))
        lines.append(",\n".join(body))
        lines.append(");")
        return "\n".join(lines)


def infer_types(info: StreamInfo) -> ColumnTypes:
    """Scan the whole file once and choose the narrowest type per column.

    Numeric-only columns become INTEGER when every value is integral and
    DOUBLE PRECISION otherwise; date columns become DATE or TIMESTAMP;
    anything else, including mixed columns, becomes TEXT.
    """
    header: list[str] = []
    saw: list[set[str]] = []
    nullable: list[bool] = []
    for rec in iter_records(info):
        if not header:
            header = rec.fields
            saw = [set() for _ in header]
            nullable = [False] * len(header)
            continue
        for i, raw in enumerate(rec.fields):
            if i >= len(saw):
                continue
            if values.is_null_like(raw):
                nullable[i] = True
                continue
            d = values.classify_date(raw)
            if d is not None:
                saw[i].add("timestamp" if d.has_time else "date")
                continue
            n = values.classify_number(raw)
            if n is not None:
                if n.kind == "int":
                    saw[i].add("int")
                elif n.kind in ("currency", "percent"):
                    saw[i].add("money")
                else:
                    saw[i].add("float")
                continue
            saw[i].add("text")
    types: list[str] = []
    for kinds in saw:
        useful = {k for k in kinds if k}
        if not useful:
            types.append("TEXT")
        elif "text" in useful:
            types.append("TEXT")
        elif "money" in useful:
            types.append("DOUBLE PRECISION")
        elif useful == {"int"}:
            types.append("INTEGER")
        elif useful <= {"int", "float"}:
            types.append("DOUBLE PRECISION")
        elif useful <= {"date", "timestamp"} and "timestamp" in useful:
            types.append("TIMESTAMP")
        elif useful == {"date"}:
            types.append("DATE")
        else:
            types.append("TEXT")
    if not header:
        return ColumnTypes([], [], [], [])
    return ColumnTypes(
        header=header,
        names=sql_column_names(header),
        sql_types=types,
        nullable=nullable,
    )


def _sql_literal(raw: str, sql_type: str) -> str:
    if values.is_null_like(raw):
        return "NULL"
    text = values.strip_invisible(raw).strip()
    if sql_type in ("INTEGER", "DOUBLE PRECISION"):
        n = values.classify_number(text)
        if n is not None and n.value is not None:
            if sql_type == "INTEGER":
                return str(int(n.value))
            return repr(float(n.value))
    if sql_type in ("DATE", "TIMESTAMP"):
        d = values.classify_date(text)
        if d is not None and d.value is not None:
            return "'" + d.value.isoformat() + "'"
    return "'" + text.replace("'", "''") + "'"


def write_sql(
    info: StreamInfo,
    out: IO[str],
    *,
    table: str = "csv_data",
    batch_size: int = 500,
    include_create: bool = True,
    dialect: str = "postgres",
) -> int:
    """Write CREATE TABLE plus batched INSERTs; returns the row count.

    ``dialect`` only changes quoting and the ``DOUBLE PRECISION`` spelling;
    the generated script is plain SQL either way.
    """
    types = infer_types(info)
    if not types.header:
        return 0
    ident = _sql_ident(table, "csv_data")
    double = "DOUBLE PRECISION" if dialect != "sqlite" else "REAL"
    if include_create:
        out.write(types.create_table(ident).replace("DOUBLE PRECISION", double) + "\n")
    cols = ", ".join(types.names)
    rows = 0
    batch: list[str] = []
    header_seen = False
    for rec in iter_records(info):
        if not header_seen:
            header_seen = True
            continue
        # Ragged rows are padded with NULL / trimmed to the header width so the
        # generated script stays valid even for a damaged source file.
        row = list(rec.fields[: len(types.names)])
        while len(row) < len(types.names):
            row.append("")
        values_sql = [
            _sql_literal(raw, types.sql_types[i]) for i, raw in enumerate(row)
        ]
        batch.append("(" + ", ".join(values_sql) + ")")
        rows += 1
        if len(batch) >= batch_size:
            out.write("INSERT INTO " + ident + " (" + cols + ") VALUES\n")
            out.write(",\n".join(batch) + ";\n")
            batch = []
    if batch:
        out.write("INSERT INTO " + ident + " (" + cols + ") VALUES\n")
        out.write(",\n".join(batch) + ";\n")
    return rows


def write_json(
    info: StreamInfo,
    out: IO[str],
    *,
    indent: int | None = None,
) -> int:
    """Stream records as a JSON array of objects; returns the row count.

    Output is a valid JSON array even for zero rows, and repeated headers are
    suffixed so no key is silently dropped.
    """
    header: list[str] = []
    keys: list[str] = []
    rows = 0
    out.write("[")
    for rec in iter_records(info):
        if not header:
            header = rec.fields
            keys = json_key_names(header)
            continue
        obj = {}
        for i, raw in enumerate(rec.fields):
            key = keys[i] if i < len(keys) else "extra_" + str(i + 1)
            obj[key] = raw
        if rows:
            out.write(",")
        if indent is None:
            out.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        else:
            body = json.dumps(obj, ensure_ascii=False, indent=indent)
            out.write("\n" + " " * indent + body.replace("\n", "\n" + " " * indent))
        rows += 1
    if indent is not None and rows:
        out.write("\n")
    out.write("]\n")
    return rows


def export_path(path: str) -> StreamInfo:
    """Sniff a file for export (thin alias kept for a readable call site)."""
    return inspect(path)
