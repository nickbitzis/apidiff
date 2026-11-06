"""Command line entry point: `apidiff OLD NEW`."""

from __future__ import annotations

import argparse
import sys

from . import __version__, report
from .diff import BREAKING, WARNING, diff, load_spec

FAIL_LEVELS = {"breaking": {BREAKING}, "warning": {BREAKING, WARNING}, "never": set()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apidiff", description="Find breaking changes between two OpenAPI 3 specs.")
    p.add_argument("--version", action="version", version=f"apidiff {__version__}")
    p.add_argument("old", help="the spec clients use today (YAML or JSON)")
    p.add_argument("new", help="the proposed spec")
    p.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    p.add_argument("--fail-on", choices=tuple(FAIL_LEVELS), default="breaking", help="exit 1 when changes at this level exist (default: breaking)")
    p.add_argument("--hide-info", action="store_true", help="leave non-breaking changes out of the report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        changes = diff(load_spec(args.old), load_spec(args.new))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(report.as_json(changes))
    elif args.format == "markdown":
        print(report.markdown(changes, not args.hide_info))
    else:
        print(report.text(changes, not args.hide_info))
    return 1 if any(c.level in FAIL_LEVELS[args.fail_on] for c in changes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
