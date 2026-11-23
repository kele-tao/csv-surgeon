# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | yes |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Email the maintainer
at the address on the GitHub profile, or use the "Report a vulnerability"
button on the repository Security tab. You will get an answer within seven days.

## Threat model

`csv-surgeon` parses untrusted files locally. It never executes cell contents and
never uploads data; there is no network code in the package. The main risks are
resource exhaustion, and the walker addresses them explicitly:

- Records are capped at 16 MiB (`MAX_RECORD_BYTES`) so one pathological quote
  cannot grow a record without bound.
- The duplicate-row index is capped at 200,000 rows and the per-column distinct
  index at 50,000 values; when a cap is hit the report says so instead of
  quietly returning a wrong number.
- `repair` refuses to write over its own input and never follows a path you did
  not pass explicitly.
