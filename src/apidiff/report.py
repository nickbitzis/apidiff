"""Render a list of changes as text, Markdown, or JSON."""

from __future__ import annotations

import json
from collections import Counter

from .diff import BREAKING, INFO, LEVELS, WARNING, Change, suggest_bump

TITLES = {BREAKING: "Breaking", WARNING: "Warnings", INFO: "Other changes"}


def counts(changes: list[Change]) -> dict[str, int]:
    c = Counter(ch.level for ch in changes)
    return {lvl: c.get(lvl, 0) for lvl in LEVELS}


def summary_line(changes: list[Change]) -> str:
    n = counts(changes)
    return f"{n[BREAKING]} breaking, {n[WARNING]} warnings, {n[INFO]} other. Suggested version bump: {suggest_bump(changes)}."


def text(changes: list[Change], show_info: bool = True) -> str:
    if not changes:
        return "No changes to the API surface."
    lines = [summary_line(changes)]
    for lvl in LEVELS:
        group = [c for c in changes if c.level == lvl]
        if not group or (lvl == INFO and not show_info):
            continue
        lines += ["", f"{TITLES[lvl]} ({len(group)})"]
        width = max(len(c.where) for c in group)
        lines += [f"  {c.where.ljust(width)}  {c.detail}  [{c.code}]" for c in group]
    return "\n".join(lines)


def markdown(changes: list[Change], show_info: bool = True) -> str:
    """Suitable for a pull request comment."""
    if not changes:
        return "### API changes\n\nNo changes to the API surface."
    n = counts(changes)
    line = summary_line(changes)
    out = ["### API changes\n\n" + (f"**{line}**" if n[BREAKING] else line)]
    for lvl in LEVELS:
        group = [c for c in changes if c.level == lvl]
        if not group or (lvl == INFO and not show_info):
            continue
        rows = "\n".join(f"| `{c.where}` | {_cell(c.detail)} | `{c.code}` |" for c in group)
        table = f"| Where | Change | Rule |\n|---|---|---|\n{rows}"
        if lvl == INFO:
            out.append(f"<details><summary>{TITLES[lvl]} ({len(group)})</summary>\n\n{table}\n\n</details>")
        else:
            out.append(f"#### {TITLES[lvl]} ({len(group)})\n\n{table}")
    return "\n\n".join(out)


def _cell(s: str) -> str:
    return s.replace("|", "\\|")


def as_json(changes: list[Change]) -> str:
    return json.dumps(
        {"summary": counts(changes), "suggested_bump": suggest_bump(changes), "changes": [c.to_dict() for c in changes]},
        indent=2,
    )
