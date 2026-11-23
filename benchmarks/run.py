"""Reproducible benchmark for csv-surgeon.

Generates a deterministic CSV of a requested size, then measures wall time and
Python peak allocation for ``lint``, ``profile`` and dry-run ``repair``.
Results are written to ``benchmarks/results.json`` and ``benchmarks/RESULTS.md``.

  python benchmarks/run.py                 # 5 MB, what the README table shows
  python benchmarks/run.py --mb 500        # the memory test
  python benchmarks/run.py --mb 5 --repeat 3
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import tracemalloc

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from csv_surgeon.lint import lint  # noqa: E402
from csv_surgeon.profile import profile  # noqa: E402
from csv_surgeon.repair import ALL_FIXES, repair  # noqa: E402

DATA = ROOT / "benchmarks" / "data"
RESULTS_JSON = ROOT / "benchmarks" / "results.json"
RESULTS_MD = ROOT / "benchmarks" / "RESULTS.md"

STATUSES = ("shipped", "pending", "cancelled", "refunded")
CUSTOMERS = ("Acme Corp", "Globex", "Initech", "Umbrella", "Hooli", "Soylent")


def generate(path: pathlib.Path, target_bytes: int) -> int:
    """Write a deterministic CSV of roughly ``target_bytes``; return the row count.

    The data is deliberately Excel-flavoured: a BOM, CRLF endings, mixed date
    spellings, currency amounts and the occasional padded value.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    row = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\ufeff")
        header = "order_id,customer,order_date,amount,status,notes\r\n"
        fh.write(header)
        written += len(header.encode("utf-8"))
        while written < target_bytes:
            row += 1
            day = row % 28 + 1
            if row % 3 == 0:
                date = f"02/{day:02d}/2024"
            elif row % 3 == 1:
                date = f"2024-01-{day:02d}"
            else:
                date = f"Jan {day} 2024"
            amount = row % 1000 + 0.5
            note = " ok " if row % 500 == 0 else "ok"
            line = "{},{},{},${},{},{}\r\n".format(
                row,
                CUSTOMERS[row % len(CUSTOMERS)],
                date,
                format(amount, ",.2f"),
                STATUSES[row % len(STATUSES)],
                note,
            )
            fh.write(line)
            written += len(line.encode("utf-8"))
    return row


def measure_time(label: str, func) -> dict[str, object]:
    """Time func without instrumentation, returning wall seconds."""
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    rows = result.rows if hasattr(result, "rows") else 0
    return {"label": label, "seconds": round(elapsed, 3), "rows": rows}


def measure_memory(func) -> float:
    """Peak Python allocation of one run, in MiB, via tracemalloc.

    Measured in a separate pass because tracemalloc roughly doubles wall time
    and would make the timing table meaningless.
    """
    tracemalloc.start()
    func()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return round(peak / (1024 * 1024), 2)


def run(size_mb: int, repeat: int) -> dict[str, object]:
    target = size_mb * 1024 * 1024
    data_dir = DATA / (f"bench-{size_mb}mb")
    path = data_dir / "input.csv"
    rows = generate(path, target)
    size = path.stat().st_size
    cases = [
        ("lint", lambda: lint(str(path))),
        ("profile", lambda: profile(str(path))),
        ("repair (dry run)", lambda: repair(str(path), ALL_FIXES)),
    ]
    summary = []
    for label, func in cases:
        best = min(measure_time(label, func)["seconds"] for _ in range(repeat))
        peak = measure_memory(func)
        summary.append(
            {
                "operation": label,
                "seconds": round(best, 3),
                "rows_per_second": int(rows / best) if best else 0,
                "mb_per_second": round(size / (1024 * 1024) / best, 1) if best else 0,
                "peak_alloc_mb": peak,
            }
        )
    return {
        "file_mb": round(size / (1024 * 1024), 1),
        "rows": rows,
        "repeat": repeat,
        "rig": f"{sys.platform} {sys.version.split()[0]}",
        "operations": summary,
    }

def markdown(result: dict[str, object]) -> str:
    lines = [
        "| Operation | Runtime | Throughput | Rows/s | Peak Python alloc |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for op in result["operations"]:  # type: ignore[union-attr]
        lines.append(
            "| {} | {:.2f}s | {:.1f} MB/s | {} | {:.1f} MB |".format(
                                op["operation"],
                op["seconds"],
                op["mb_per_second"],
                format(op["rows_per_second"], ","),
                op["peak_alloc_mb"],
            )
        )
    body = "\n".join(lines)
    return (
        "# Benchmark\n\n"
        "Generated by `python benchmarks/run.py` on "
        + str(result["rig"])
        + ".\n\n"
        "Input: **"
        + str(result["file_mb"])
        + " MB**, "
        + format(int(result["rows"]), ",")  # type: ignore[arg-type]
        + " data rows, best of "
        + str(result["repeat"])
        + ".\n\n"
        + body
        + "\n\nPeak Python allocation is measured with `tracemalloc`; it does not"
        "\ninclude the interpreter or the CSV text buffer of the current line.\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mb", type=int, default=5, help="input size in MiB (default 5)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per operation (default 1)")
    ap.add_argument("--no-write", action="store_true", help="print only, do not update results")
    args = ap.parse_args()
    result = run(args.mb, max(1, args.repeat))
    text = markdown(result)
    print(text)
    if not args.no_write:
        RESULTS_JSON.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        RESULTS_MD.write_text(text, encoding="utf-8")
        print("wrote", RESULTS_JSON.name, "and", RESULTS_MD.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
