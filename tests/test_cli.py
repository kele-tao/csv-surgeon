"""CLI behaviour: exit codes, output formats and friendly errors."""

from __future__ import annotations

import json

import pytest

from csv_surgeon.cli import main


def run(capsys, argv):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "csv-surgeon" in capsys.readouterr().out


def test_no_command_is_usage_error(capsys):
    code, out, _ = run(capsys, [])
    assert code == 2
    assert "lint" in out


def test_lint_exit_zero_on_clean_file(capsys, clean_csv):
    code, out, _ = run(capsys, ["lint", str(clean_csv), "--no-color"])
    assert code == 0
    assert "clean" in out


def test_lint_exit_one_on_errors(capsys, messy_csv):
    code, out, _ = run(capsys, ["lint", str(messy_csv), "--no-color"])
    assert code == 1
    assert "CS004" in out


def test_lint_fail_on_never(capsys, messy_csv):
    code, _, _ = run(capsys, ["lint", str(messy_csv), "--fail-on", "never"])
    assert code == 0


def test_lint_json_is_parseable(capsys, messy_csv):
    code, out, _ = run(capsys, ["lint", str(messy_csv), "--json"])
    payload = json.loads(out)
    assert code == 1
    assert payload["counts"]["error"] >= 2
    assert payload["delimiter"] == ";"


def test_lint_missing_file_is_usage_error(capsys):
    code, _, err = run(capsys, ["lint", "does-not-exist.csv"])
    assert code == 2
    assert "no such file" in err
    assert "Traceback" not in err


def test_lint_directory_is_usage_error(capsys, tmp_path):
    code, _, err = run(capsys, ["lint", str(tmp_path)])
    assert code == 2
    assert "directory" in err


def test_profile_output(capsys, messy_csv):
    code, out, _ = run(capsys, ["profile", str(messy_csv), "--no-color"])
    assert code == 0
    assert "order_id" in out
    assert "top" in out


def test_profile_json(capsys, messy_csv):
    _, out, _ = run(capsys, ["profile", str(messy_csv), "--json"])
    payload = json.loads(out)
    assert payload["columns"] == 6
    assert payload["rows"] == 8


def test_repair_dry_run_then_write(capsys, messy_csv, tmp_path):
    out_path = tmp_path / "clean.csv"
    code, out, _ = run(capsys, ["repair", str(messy_csv), "--no-color"])
    assert code == 0
    assert "dry run" in out
    code, out, _ = run(
        capsys, ["repair", str(messy_csv), "--fix", "all", "--out", str(out_path), "--no-color"]
    )
    assert code == 0
    assert out_path.exists()
    assert "UmbrellaCorp" in out_path.read_text(encoding="utf-8")


def test_repair_refuses_to_overwrite_input(capsys, messy_csv):
    code, _, err = run(capsys, ["repair", str(messy_csv), "--out", str(messy_csv)])
    assert code == 2
    assert "refusing to overwrite" in err


def test_repair_unknown_fix_is_usage_error(capsys, messy_csv):
    code, _, err = run(capsys, ["repair", str(messy_csv), "--fix", "magic"])
    assert code == 2
    assert "unknown repair" in err


def test_repair_json_and_log(capsys, messy_csv, tmp_path):
    log = tmp_path / "log.json"
    _, out, _ = run(capsys, ["repair", str(messy_csv), "--json", "--log", str(log)])
    assert json.loads(out)["counts"]["dedupe"] == 1
    payload = json.loads(log.read_text(encoding="utf-8"))
    assert payload["input"].endswith("messy.csv")


def test_to_json_stdout(capsys, clean_csv):
    code, out, _ = run(capsys, ["to-json", str(clean_csv)])
    assert code == 0
    assert json.loads(out)[0]["name"] == "Alice"


def test_to_json_out_file(capsys, clean_csv, tmp_path):
    out_path = tmp_path / "out.json"
    code, _, err = run(capsys, ["to-json", str(clean_csv), "--out", str(out_path)])
    assert code == 0
    assert "2 rows" in err
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload[1]["id"] == "2"


def test_to_sql_table_name_and_batch(capsys, clean_csv):
    code, out, _ = run(capsys, ["to-sql", str(clean_csv), "--table", "orders", "--batch", "1"])
    assert code == 0
    assert "CREATE TABLE orders" in out
    assert out.count("INSERT INTO orders") == 2


def test_to_sql_no_create(capsys, clean_csv):
    _, out, _ = run(capsys, ["to-sql", str(clean_csv), "--no-create"])
    assert "CREATE TABLE" not in out


def test_rules_table(capsys):
    code, out, _ = run(capsys, ["rules", "--no-color"])
    assert code == 0
    assert "CS001" in out and "CS015" in out


def test_rules_json(capsys):
    _, out, _ = run(capsys, ["rules", "--json"])
    payload = json.loads(out)
    assert len(payload) == 15
    assert payload[0]["code"] == "CS001"


def test_unicode_file_does_not_crash(capsys, tmp_path):
    path = tmp_path / "u.csv"
    path.write_bytes("id,name\n1,\u5f20\u4e09\U0001f600\n".encode())
    code, out, _ = run(capsys, ["profile", str(path), "--no-color"])
    assert code == 0
    assert "\u5f20\u4e09" in out


def test_empty_file_lint(capsys, tmp_path):
    path = tmp_path / "e.csv"
    path.write_bytes(b"")
    code, out, _ = run(capsys, ["lint", str(path), "--no-color"])
    assert code == 0
    assert "file is empty" in out
