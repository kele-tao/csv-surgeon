# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-23

First public release.

### Added

- `lint` with 15 documented rule codes (`CS001`-`CS015`), each with a severity,
  a physical line/column and up to five real examples.
- `profile` reporting type mix, null patterns, top values, distinct counts,
  numeric/date ranges, longest values and case/space near-duplicates.
- `repair` with nine safe, documented fixes, dry-run by default and a JSON
  change log; it refuses to overwrite its input.
- `to-json` and `to-sql` (CREATE TABLE with column-wide type inference plus
  batched INSERTs, `postgres` and `sqlite` dialects).
- Streaming reader with BOM-aware encoding detection (UTF-8/UTF-16/Latin-1)
  and delimiter sniffing that scores header consistency.
- `rules` subcommand that prints the rule reference as a table or JSON.
- Benchmark harness producing `benchmarks/results.json` and `RESULTS.md`.

### Notes

- Python 3.10+, stdlib only, no runtime dependencies.
- Exit codes: 0 ok, 1 lint gate failed, 2 usage error.
