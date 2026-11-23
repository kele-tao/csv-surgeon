# Contributing

Thanks for taking the time to contribute.

## Development setup

```bash
git clone https://github.com/kele-tao/csv-surgeon
cd csv-surgeon
python -m venv .venv
. .venv/Scripts/activate        # Windows; use .venv/bin/activate on Unix
pip install -e ".[dev]"
```

## Before you open a pull request

```bash
pytest -q          # all tests must pass, no network access needed
ruff check .       # style gate, line length 100
python -m csv_surgeon lint examples/messy.csv
```

The test suite is the specification: if you change behaviour, change the test
that documents it in the same commit.

## Adding a lint rule

1. Pick the next free `CS0xx` code and add it to `RULES` in `src/csv_surgeon/lint.py`
   with a title, a severity and whether a repair exists.
2. Emit a `Finding` from the streaming pass. Findings are aggregated: report a
   count plus up to five examples, never one finding per row.
3. Add a positive test to `tests/test_lint.py` and, if the rule is fixable, a
   repair test to `tests/test_repair.py`.
4. Add the code to the rule table in `README.md`.

## Reporting bugs

A bug report is only actionable with the input file. Please attach the smallest
CSV that reproduces the problem (redact anything private) and the exact command.
