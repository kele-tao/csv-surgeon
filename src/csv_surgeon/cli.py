"""Command line interface for csv-surgeon.

Exit codes are part of the contract:

* ``0`` - success, nothing to report (or reporting was disabled).
* ``1`` - the lint gate failed (findings at or above ``--fail-on``).
* ``2`` - usage error: bad flag, missing file, unreadable input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from . import __version__, render
from .csvio import inspect
from .export import write_json, write_sql
from .lint import LintReport, rule_table
from .profile import ProfileReport, profile
from .repair import ALL_FIXES, FixError, parse_fixes, repair, write_change_log

PROG = "csv-surgeon"


class UsageError(Exception):
    """A user-facing error that exits with code 2."""


def _delim_label(delimiter: str) -> str:
    names = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
    return names.get(delimiter, repr(delimiter))


def _readable(path: str) -> str:
    if not os.path.exists(path):
        raise UsageError("no such file: " + path)
    if os.path.isdir(path):
        raise UsageError("expected a file but got a directory: " + path)
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        raise UsageError("cannot read " + path + ": " + exc.strerror) from exc
    return path


def cmd_lint(args: argparse.Namespace, out) -> int:
    path = _readable(args.path)
    report = lint_file(path)
    if args.json:
        json.dump(report.as_dict(), out, indent=2, ensure_ascii=False)
        out.write("\n")
    else:
        out.write(_render_lint(report, colour=not args.no_color, width=args.width))
        out.write("\n")
    threshold = args.fail_on
    if threshold == "never":
        return 0
    hits = len(report.by_severity(threshold))
    if threshold == "error":
        hits = report.error_count
    return 1 if hits else 0


def lint_file(path: str) -> LintReport:
    from .lint import lint

    return lint(path)


def _render_lint(report: LintReport, *, colour: bool, width: int = 100) -> str:
    c = colour
    info = report.info
    enc = info.encoding_guess
    lines: list[str] = []
    lines.append("")
    lines.append(
        "  "
        + render.paint("csv-surgeon", render.BOLD, render.CYAN, enabled=c)
        + render.paint(" lint  ", render.GREY, enabled=c)
        + render.paint(report.path, render.BOLD, enabled=c)
    )
    lines.append("")
    meta = [
        ("rows", str(report.rows)),
        ("columns", str(report.columns)),
        ("encoding", (enc.label if enc else info.encoding)),
        ("delimiter", _delim_label(info.delimiter)),
        ("newlines", info.newline_style),
    ]
    for key, value in meta:
        lines.append(
            "  " + render.paint(key.ljust(10), render.GREY, enabled=c) + "  " + value
        )
    lines.append("")
    if not report.findings:
        lines.append(
            "  "
            + render.paint("clean", render.BOLD, render.BRIGHT_GREEN, enabled=c)
            + "  0 findings, 0 instances"
        )
        return "\n".join(lines)
    counts = report.counts()
    lines.append(
        "  " + render.paint("SEVERITY  RULE   INSTANCES  FINDING", render.BOLD, enabled=c)
    )
    for f in report.findings:
        sev = render.paint(
            render.SEVERITY_LABEL[f.severity].ljust(8),
            render.SEVERITY_COLOUR[f.severity],
            render.BOLD,
            enabled=c,
        )
        code = render.paint(f.code, render.BOLD, enabled=c)
        used = ("x" + str(f.count)) if f.count > 1 else "-"
        lines.append(
            "  "
            + sev
            + "  "
            + code
            + "  "
            + used.ljust(9)
            + "  "
            + f.message
        )
        for line_no, example in f.examples[:2]:
            lines.append(
                "            " + render.paint("line " + str(line_no), render.GREY, enabled=c)
                + "  " + render.paint(example, render.GREY, enabled=c)
            )
    lines.append("")
    summary = "  " + "   ".join(
        [
            render.paint(
                str(counts["error"]) + " error", render.BRIGHT_RED, render.BOLD, enabled=c
            ),
            render.paint(
                str(counts["warn"]) + " warn", render.BRIGHT_YELLOW, render.BOLD, enabled=c
            ),
            render.paint(str(counts["info"]) + " info", render.BRIGHT_BLUE, render.BOLD, enabled=c),
            render.paint(
                str(counts["instances"]) + " instances in " + str(report.rows) + " rows",
                render.GREY,
                enabled=c,
            ),
        ]
    )
    lines.append(summary)
    total = max(1, counts["error"] + counts["warn"] + counts["info"])
    lines.append(
        "  " + render.bar(counts["error"], total, 10, enabled=c) + " errors   "
        + render.bar(counts["warn"], total, 10, enabled=c) + " warnings"
    )
    [f.rule.title for f in report.findings[:3]]
    lines.append(
        "  " + render.paint("next:", render.GREY, enabled=c) + " "
        + render.paint("run repair with --fix for the " + str(
            sum(1 for f in report.findings if f.fixable)
        ) + " fixable rule(s)", render.GREY, enabled=c)
    )
    for note in report.notes:
        lines.append("  " + render.paint("note: " + note, render.GREY, enabled=c))
    return "\n".join(lines)


def cmd_profile(args: argparse.Namespace, out) -> int:
    path = _readable(args.path)
    report = profile(path, top_n=args.top)
    if args.json:
        json.dump(report.as_dict(), out, indent=2, ensure_ascii=False)
        out.write("\n")
        return 0
    out.write(_render_profile(report, colour=not args.no_color, top=args.top))
    out.write("\n")
    return 0


def _render_profile(report: ProfileReport, *, colour: bool, top: int) -> str:
    c = colour
    lines = [""]
    lines.append(
        "  "
        + render.paint("csv-surgeon", render.BOLD, render.CYAN, enabled=c)
        + render.paint(" profile  ", render.GREY, enabled=c)
        + render.paint(report.path, render.BOLD, enabled=c)
    )
    lines.append(
        "  "
        + render.paint(
            str(report.rows) + " rows, " + str(len(report.header)) + " columns",
            render.GREY,
            enabled=c,
        )
    )
    for col in report.columns:
        lines.append("")
        title = str(col.index + 1) + ". " + (col.name or "(blank)")
        lines.append(
            "  " + render.paint(title, render.BOLD, render.GREEN, enabled=c)
            + "   "
            + render.paint("type " + col.dominant_type, render.CYAN, enabled=c)
            + "   "
            + render.paint("distinct " + _distinct_label(col), render.GREY, enabled=c)
        )
        if col.types:
            mix = ", ".join(
                k + " " + str(v)
                for k, v in sorted(col.types.items(), key=lambda kv: (-kv[1], kv[0]))
            )
            lines.append("      " + render.paint("mix     ", render.GREY, enabled=c) + mix)
        if col.nulls:
            nulls = ", ".join(k + " " + str(v) for k, v in sorted(col.nulls.items()))
            lines.append("      " + render.paint("nulls   ", render.GREY, enabled=c) + nulls)
        lines.append(
            "      " + render.paint("range   ", render.GREY, enabled=c)
            + _ellipsis(col.min_value, 24) + " .. " + _ellipsis(col.max_value, 24)
        )
        if col.top(top):
            top_line = _top_line(col.top(top))
            lines.append("      " + render.paint("top     ", render.GREY, enabled=c) + top_line)
        if col.longest[0]:
            lines.append(
                "      " + render.paint("longest ", render.GREY, enabled=c)
                + str(col.longest[0])
                + " chars  "
                + render.paint(col.longest[1], render.GREY, enabled=c)
            )
        if col.near_duplicates:
            pairs = "; ".join(
                " vs ".join(repr(s) for s in spellings[:2])
                for _, spellings in col.near_duplicates[:3]
            )
            lines.append(
                "      "
                + render.paint("near-dup", render.BRIGHT_YELLOW, enabled=c)
                + " "
                + pairs
            )
    if report.notes:
        lines.append("")
        for note in report.notes:
            lines.append("  " + render.paint("note: " + note, render.GREY, enabled=c))
    return "\n".join(lines)


def _distinct_label(col) -> str:
    return str(col.distinct) + "+" if col.distinct_capped else str(col.distinct)


def _repair_target(report) -> str:
    return "dry run" if report.out_path is None else "writing " + str(report.out_path)


def _ellipsis(text: str, limit: int) -> str:
    flat = text.replace("\n", "\\n")
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def _top_line(items: list[tuple[str, int]]) -> str:
    return ", ".join(_ellipsis(v, 20) + " x" + str(n) for v, n in items)


def cmd_repair(args: argparse.Namespace, out) -> int:
    path = _readable(args.path)
    try:
        fixes = parse_fixes(args.fix)
    except FixError as exc:
        raise UsageError(str(exc)) from exc
    if args.out and os.path.abspath(args.out) == os.path.abspath(path):
        raise UsageError("refusing to overwrite the input; pass a different --out path")
    report = repair(path, fixes, args.out)
    if args.log:
        write_change_log(report, args.log)
    if args.json:
        json.dump(report.as_dict(), out, indent=2, ensure_ascii=False)
        out.write("\n")
        return 0
    out.write(_render_repair(report, colour=not args.no_color))
    out.write("\n")
    return 0


def _render_repair(report, *, colour: bool) -> str:
    c = colour
    dry = report.out_path is None
    lines = [""]
    lines.append(
        "  "
        + render.paint("csv-surgeon", render.BOLD, render.CYAN, enabled=c)
        + render.paint(" repair  ", render.GREY, enabled=c)
        + render.paint(report.path, render.BOLD, enabled=c)
        + render.paint("  (" + _repair_target(report) + ")", render.GREY, enabled=c)
    )
    lines.append("  " + render.paint("fixes: " + ", ".join(report.fixes), render.GREY, enabled=c))
    lines.append("")
    if report.counts:
        rows = [[k, str(v)] for k, v in sorted(report.counts.items())]
        lines.append(render.table(["CHANGE", "COUNT"], rows, aligns=["left", "right"], enabled=c))
    else:
        lines.append("  " + render.paint("nothing to repair", render.BRIGHT_GREEN, enabled=c))
    lines.append("")
    for change in report.changes[:8]:
        arrow = "\u2192"
        lines.append(
            "  " + render.paint("line " + str(change.line).ljust(5), render.GREY, enabled=c)
            + " " + render.paint(change.action.ljust(11), render.CYAN, enabled=c)
            + " " + render.paint(change.before, render.GREY, enabled=c)
            + (" " + arrow + " " + change.after if change.after else "")
        )
    if len(report.changes) > 8:
        lines.append(
            "  "
            + render.paint(
                "... " + str(len(report.changes) - 8) + " more change(s) in the change log",
                render.GREY,
                enabled=c,
            )
        )
    lines.append("")
    verdict = (
        render.paint("dry run", render.BRIGHT_YELLOW, render.BOLD, enabled=c)
        + " - pass --out FILE to write the cleaned copy"
        if dry
        else render.paint(
            "wrote " + str(report.out_path) + " (" + str(report.rows_out) + " rows)",
            render.BRIGHT_GREEN,
            render.BOLD,
            enabled=c,
        )
    )
    lines.append("  " + verdict)
    return "\n".join(lines)


def cmd_to_json(args: argparse.Namespace, out) -> int:
    path = _readable(args.path)
    info = inspect(path)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            rows = write_json(info, fh, indent=args.indent)
        sys.stderr.write("wrote " + args.out + " (" + str(rows) + " rows)\n")
        return 0
    rows = write_json(info, out, indent=args.indent)
    if args.quiet:
        sys.stderr.write(str(rows) + " rows\n")
    return 0


def cmd_to_sql(args: argparse.Namespace, out) -> int:
    path = _readable(args.path)
    info = inspect(path)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            rows = write_sql(
                info,
                fh,
                table=args.table,
                batch_size=args.batch,
                include_create=not args.no_create,
                dialect=args.dialect,
            )
        sys.stderr.write("wrote " + args.out + " (" + str(rows) + " rows)\n")
        return 0
    write_sql(
        info,
        out,
        table=args.table,
        batch_size=args.batch,
        include_create=not args.no_create,
        dialect=args.dialect,
    )
    return 0


def cmd_rules(args: argparse.Namespace, out) -> int:
    rules = rule_table()
    if args.json:
        payload = [
            {
                "code": r.code,
                "title": r.title,
                "severity": r.severity,
                "fixable": r.fixable,
                "description": r.description,
            }
            for r in rules
        ]
        json.dump(payload, out, indent=2)
        out.write("\n")
        return 0
    rows = [
        [r.code, r.severity.upper(), "yes" if r.fixable else "no", r.title]
        for r in rules
    ]
    rendered = render.table(
        ["CODE", "SEVERITY", "FIXABLE", "CHECK"], rows, enabled=not args.no_color
    )
    out.write("\n" + rendered)
    out.write("\n\n  " + str(len(rules)) + " rules\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Lint, profile and safely repair real-world CSV files.",
        epilog="Exit codes: 0 ok, 1 lint gate failed, 2 usage error.",
    )
    parser.add_argument("--version", action="version", version=PROG + " " + __version__)
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    lint_p = subs.add_parser("lint", help="report data-quality findings")
    lint_p.add_argument("path", help="CSV file to inspect")
    lint_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    lint_p.add_argument(
        "--fail-on",
        choices=("error", "warn", "never"),
        default="error",
        help="exit 1 when a finding of this severity exists (default: error)",
    )
    lint_p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    lint_p.add_argument("--width", type=int, default=100, help=argparse.SUPPRESS)

    prof_p = subs.add_parser("profile", help="per-column statistics")
    prof_p.add_argument("path", help="CSV file to profile")
    prof_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    prof_p.add_argument("--top", type=int, default=5, help="values shown per column (default: 5)")
    prof_p.add_argument("--no-color", action="store_true", help="disable ANSI colour")

    rep_p = subs.add_parser("repair", help="apply safe, documented repairs")
    rep_p.add_argument("path", help="CSV file to repair")
    rep_p.add_argument(
        "--fix",
        default="all",
        help="comma separated repairs or all (default: all). Known: " + ", ".join(ALL_FIXES),
    )
    rep_p.add_argument(
        "--out", default=None, help="write the cleaned copy here (dry run without it)"
    )
    rep_p.add_argument("--log", default=None, help="write the machine-readable change log here")
    rep_p.add_argument("--json", action="store_true", help="emit the report as JSON")
    rep_p.add_argument("--no-color", action="store_true", help="disable ANSI colour")

    json_p = subs.add_parser("to-json", help="convert to a JSON array of objects")
    json_p.add_argument("path", help="CSV file to convert")
    json_p.add_argument("--out", default=None, help="write here instead of stdout")
    json_p.add_argument("--indent", type=int, default=None, help="pretty-print with this indent")
    json_p.add_argument("--quiet", action="store_true", help="suppress the row-count notice")

    sql_p = subs.add_parser("to-sql", help="convert to CREATE TABLE + batched INSERTs")
    sql_p.add_argument("path", help="CSV file to convert")
    sql_p.add_argument("--table", default="csv_data", help="table name (default: csv_data)")
    sql_p.add_argument("--batch", type=int, default=500, help="rows per INSERT (default: 500)")
    sql_p.add_argument("--dialect", choices=("postgres", "sqlite"), default="postgres")
    sql_p.add_argument("--no-create", action="store_true", help="omit CREATE TABLE")
    sql_p.add_argument("--out", default=None, help="write here instead of stdout")

    rules_p = subs.add_parser("rules", help="list every lint rule and its severity")
    rules_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    rules_p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    return parser


HANDLERS = {
    "lint": cmd_lint,
    "profile": cmd_profile,
    "repair": cmd_repair,
    "to-json": cmd_to_json,
    "to-sql": cmd_to_sql,
    "rules": cmd_rules,
}


def _use_utf8_console() -> None:
    """Make Windows consoles able to print box drawing and CJK output.

    sys.stdout may still be a legacy code page (cp1252/cp936) when the CLI is
    run from a script or a redirected pipe; box characters would then raise
    UnicodeEncodeError mid-report, which is worse than losing them.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by the console script and ``python -m csv_surgeon``."""
    _use_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    handler = HANDLERS[args.command]
    try:
        return handler(args, sys.stdout)
    except UsageError as exc:
        sys.stderr.write(PROG + ": error: " + str(exc) + "\n")
        sys.stderr.write("try '" + PROG + " " + args.command + " --help'\n")
        return 2
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
