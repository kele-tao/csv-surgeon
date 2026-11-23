"""csv-surgeon: lint, profile and safely repair real-world CSV files.

The package is deliberately stdlib-only. Every command streams records, so a
500 MB export is audited with a memory footprint that does not grow with the
file size (apart from the documented, capped duplicate-row index).

Public entry points:

* :func:`csv_surgeon.cli.main` - the CLI behind ``csv-surgeon``.
* :func:`csv_surgeon.lint.lint` - the rule engine used by ``lint``.
* :func:`csv_surgeon.profile.profile` - per-column profiling.
* :func:`csv_surgeon.repair.repair` - the safe repair pipeline.
* :func:`csv_surgeon.export.write_json` / :func:`csv_surgeon.export.write_sql`.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["CsvSurgeonError", "__version__"]


class CsvSurgeonError(Exception):
    """Base class for errors that should be shown to the user, not a traceback."""
