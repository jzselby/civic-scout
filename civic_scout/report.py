"""The daily Markdown report: reports/YYYY-MM-DD.md."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def cell(value) -> str:
    return ("" if value is None else str(value)).replace("|", "\\|").replace("\n", " ")


def link(item: dict, label: str | None = None) -> str:
    label = cell(label or item["id"])
    return f"[{label}]({item['url']})" if item.get("url") else label


def why(item: dict) -> str:
    note = item.get("editor_note")
    return cell(item.get("why_it_matters")) + (f" *Editor: {cell(note)}*" if note else "")


def _sort_key(item: dict):
    return (IMPORTANCE_ORDER.get(item.get("importance"), 3), item.get("date") or "")


def build(today: date, items: list[dict], labels: dict[str, str], health: dict[str, str],
          briefing: str | None, connections: dict[str, list[dict]]) -> str:
    items = [i for i in items if not i.get("hidden")]
    parts = [f"# Civic Scout briefing: {today:%B %d, %Y}\n"]
    by_source: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        by_source[i["source"]].append(i)
    high = [i for i in items if i.get("importance") == "high"]
    parts.append(f"**{len(items)} new record(s), {len(high)} high importance.**\n")
    parts.append("| Source | New | Status |\n|---|---|---|")
    for name, status in health.items():
        parts.append(f"| {cell(labels.get(name, name))} | {len(by_source.get(name, []))} | {cell(status)} |")
    parts.append("")

    if briefing:
        parts += [briefing.strip(), ""]

    if high:
        parts += ["## High importance\n", "| Source | Date | Headline | Why it matters |", "|---|---|---|---|"]
        for i in sorted(high, key=_sort_key):
            parts.append(f"| {cell(labels.get(i['source']))} | {cell(i.get('date'))} "
                         f"| {link(i, i.get('headline') or i.get('title'))} | {why(i)} |")
        parts.append("")

    if connections:
        index = {f"{i['source']}:{i['id']}": i for i in items}
        parts.append("## Connections across records\n")
        for key, matches in connections.items():
            item = index.get(key)
            if not item:
                continue
            parts.append(f"- {link(item, item.get('headline') or item.get('title'))} "
                         f"({cell(labels.get(item['source']))}) matches:")
            for m in matches[:6]:
                label = f"[{cell(m['title'])}]({m['url']})" if m.get("url") else cell(m["title"])
                parts.append(f"  - {label} ({cell(labels.get(m['source'], m['source']))}, "
                             f"{cell(m.get('date'))}) on {cell(', '.join(sorted(set(m['via']))))}")
        parts.append("")

    parts.append("## All new records\n")
    for name in labels:
        group = by_source.get(name)
        if not group:
            continue
        parts += [f"### {labels[name]} ({len(group)})\n",
                  "| Importance | Date | Record | Who | Where | Why it matters |", "|---|---|---|---|---|---|"]
        for i in sorted(group, key=_sort_key):
            parts.append(f"| {cell(i.get('importance'))} | {cell(i.get('date'))} "
                         f"| {link(i, i.get('headline') or i.get('title'))} | {cell(i.get('org'))} "
                         f"| {cell(i.get('place'))} | {why(i)} |")
        parts.append("")
    return "\n".join(parts)
