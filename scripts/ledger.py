#!/usr/bin/env python3
"""Журнал ревизий docs/audits/ledger.md мишени.

  ledger.py <мишень> status   — есть ли журнал, дата и коммит последней ревизии, число открытых
  ledger.py <мишень> open     — открытые находки (id, серьёзность, семейство, название)
  ledger.py <мишень> next     — следующий свободный id (AA-NNN)
  ledger.py <мишень> stamp <дата> <коммит> [режим] [--report имя.md]  — записать ревизию в шапку (создаёт журнал, если нет)

Формат строки журнала — references/report-template.md §Журнал. Id никогда не переиспользуются:
next = max(id)+1 по всем строкам, включая закрытые и отклонённые.
"""
import argparse
from datetime import date as calendar_date
import os
import re
import sys

HEADER = """# Журнал ревизий agent-audit

## Ревизии
| дата | коммит | режим | отчёт |
|---|---|---|---|

## Находки
| id | fingerprint | семейство | серьёзность | статус | открыта | последняя | закрыта в | название |
|---|---|---|---|---|---|---|---|---|
"""

ROW = re.compile(r"^\|\s*(AA-\d+)\s*\|(.*)$")


def path_for(target):
    return os.path.join(os.path.abspath(target), "docs", "audits", "ledger.md")


def rows(text):
    out = []
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 9:
                out.append(dict(id=cells[0], fingerprint=cells[1], family=cells[2], sev=cells[3],
                                status=cells[4], opened=cells[5], last=cells[6], closed=cells[7],
                                title=cells[8]))
    return out


def audits(text):
    out = []
    in_sec = False
    for line in text.splitlines():
        if line.startswith("## Ревизии"):
            in_sec = True
            continue
        if line.startswith("## ") and in_sec:
            break
        if in_sec and line.startswith("|") and not line.startswith("|---") and "дата" not in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0]:
                out.append(cells)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    commands = parser.add_subparsers(dest="cmd", required=True)
    for command in ("status", "open", "next"):
        commands.add_parser(command)
    stamp = commands.add_parser("stamp")
    stamp.add_argument("date")
    stamp.add_argument("commit")
    stamp.add_argument("mode", nargs="?", default="")
    stamp.add_argument("--report", help="Имя отчёта; по умолчанию дата[-N].md")
    args = parser.parse_args()
    target, cmd = args.target, args.cmd
    p = path_for(target)
    exists = os.path.exists(p)
    text = open(p, encoding="utf-8").read() if exists else ""
    if cmd == "status":
        if not exists:
            print("журнал: нет → режим «первая»")
            return
        a = audits(text)
        last = max(a, key=lambda row: row[0]) if a else None  # newest date; first row wins ties
        opened = [r for r in rows(text) if r["status"] in ("открыто", "регресс")]
        print(f"журнал: {p}")
        print(f"ревизий: {len(a)}; последняя: {last[0] if last else '—'} коммит {last[1] if last else '—'}")
        print(f"открытых находок: {len(opened)}; всего строк: {len(rows(text))} → режим «повторная» (или «дифф» по слову владельца)")
    elif cmd == "open":
        for r in rows(text):
            if r["status"] in ("открыто", "регресс"):
                print(f"{r['id']} {r['sev']} сем.{r['family']} [{r['status']}] {r['title']}  ({r['fingerprint']})")
    elif cmd == "next":
        ids = [int(r["id"].split("-")[1]) for r in rows(text)]
        print(f"AA-{(max(ids) + 1 if ids else 1):03d}")
    elif cmd == "stamp":
        date, commit, mode = args.date, args.commit, args.mode
        try:
            calendar_date.fromisoformat(date)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
        except ValueError:
            parser.error("дата должна быть корректной датой в формате YYYY-MM-DD")
        if any("|" in value or "\n" in value for value in (commit, mode, args.report or "")):
            parser.error("поля ревизии не могут содержать | или перенос строки")
        existing = audits(text)
        duplicate = next((row for row in existing if row[:3] == [date, commit, mode]
                          and (args.report is None or len(row) > 3 and row[3] == args.report)), None)
        if duplicate:
            print(f"уже записано: {' | '.join(duplicate)}")
            return
        used = {row[3] for row in existing if len(row) > 3}
        report = args.report or f"{date}.md"
        if args.report and report in used:
            parser.error("имя отчёта уже используется другой ревизией")
        suffix = 2
        while not args.report and report in used:
            report = f"{date}-{suffix}.md"
            suffix += 1
        if not exists:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            text = HEADER
        line = f"| {date} | {commit} | {mode} | {report} |"
        separator = "|---|---|---|---|\n"
        if separator not in text:
            parser.error("не найден заголовок таблицы ревизий; журнал не изменён")
        text = text.replace(separator, f"{separator}{line}\n", 1)
        open(p, "w", encoding="utf-8").write(text)
        print(f"записано: {line} → {p}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
