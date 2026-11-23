"""Shared fixtures for the csv-surgeon test suite."""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


@pytest.fixture()
def messy_csv() -> pathlib.Path:
    """The bundled semi-broken example file."""
    return EXAMPLES / "messy.csv"


@pytest.fixture()
def clean_csv(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "clean.csv"
    text = "id,name,amount\n1,Alice,10.5\n2,Bob,20\n"
    path.write_text(text, encoding="utf-8", newline="")
    return path


@pytest.fixture()
def write_csv(tmp_path: pathlib.Path):
    """Return a helper that writes raw bytes and hands back the path."""

    def _write(name: str, data: bytes) -> pathlib.Path:
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return _write
