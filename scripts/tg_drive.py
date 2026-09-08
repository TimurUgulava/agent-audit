#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живой прогон агента в Telegram от аккаунта владельца (agent-audit, live-drive.md).

Пишет ТОЛЬКО названному боту: сущность без признака bot отвергается, людям и чатам
писать нельзя. Авторизация — согласованный план прогона текущей ревизии.
Нужен Telethon >= 1.44 (`pip install telethon`). Учётные данные приложения — переменные
окружения TG_API_ID и TG_API_HASH (my.telegram.org); файл сессии — TG_SESSION или --session
(по умолчанию ~/.agent-audit/tg-drive.session). При первом запуске Telethon спросит телефон
и код входа; дальше сессия переиспользуется. Секреты не передаются аргументами и не печатаются.

  tg_drive.py --bot @имя read [N]
  tg_drive.py --bot @имя send "<текст>" [--wait S] [--total S]
  tg_drive.py --bot @имя click <msg_id> "<кнопка>" [--wait S] [--total S]
  tg_drive.py --bot @имя wait <after_id> [--wait S] [--total S]
Общие флаги: --transcript файл.jsonl (дописывать запись каждого шага — источник live),
--session путь (файл сессии Telethon).

Rich-сообщения бота (sendRichMessage) Telethon не разбирает: полный текст читай в точке
чтения из карты (база, лог, экспорт). Ожидание: --wait секунд тишины после последнего
изменения, но не дольше --total.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

DEFAULT_SESSION = os.path.expanduser("~/.agent-audit/tg-drive.session")


def get_client(session=None):
    """Telethon-клиент из переменных окружения; без них — понятный отказ, не трейсбек."""
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("задай TG_API_ID и TG_API_HASH (my.telegram.org) в окружении; "
                         "сессия — TG_SESSION или --session")
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        raise SystemExit("нужен Telethon >= 1.44: pip install telethon")
    session = session or os.environ.get("TG_SESSION") or DEFAULT_SESSION
    os.makedirs(os.path.dirname(os.path.abspath(session)), exist_ok=True)
    return TelegramClient(session, int(api_id), api_hash)


def fmt(m):
    """Сообщение Telethon → словарь для печати и транскрипта."""
    buttons = []
    for row in (getattr(m, "buttons", None) or []):
        buttons.append([b.text for b in row])
    sender = getattr(m, "sender", None)
    who = "бот" if (sender is not None and getattr(sender, "bot", False)) else "я"
    date = getattr(m, "date", None)
    media = getattr(m, "media", None)
    return {
        "id": m.id,
        "who": who,
        "date": date.strftime("%H:%M:%S") if date else "",
        "text": (getattr(m, "message", None) or ("<медиа>" if media else "")),
        "buttons": buttons,
        "edited": bool(getattr(m, "edit_date", None)),
    }


def show(msgs):
    for m in msgs:
        d = fmt(m)
        print(f"[{d['id']}] {d['date']} {d['who']}: {d['text']}")
        for row in d["buttons"]:
            print("      кнопки:", " | ".join(row))


def find_button(rows, query):
    """Первая кнопка, текст которой содержит query без учёта регистра; None, если нет."""
    q = query.lower()
    for row in rows or []:
        for b in row:
            if q in b.text.lower():
                return b
    return None


def ensure_bot(entity, name):
    """Прогон пишет только боту: у сущности должен быть признак bot."""
    if not getattr(entity, "bot", False):
        raise SystemExit(f"«{name}» не бот — прогон пишет только названному боту")
    return entity


def append_transcript(path, record):
    """Дописать одну JSON-строку; без path — ничего."""
    if not path:
        return
    record = dict(record)
    record.setdefault("ts", dt.datetime.now().isoformat(timespec="seconds"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def wait_new(client, entity, after_id, quiet, total=600):
    """Сообщения с id > after_id; вернуть, когда quiet секунд без изменений (или total).

    Возвращает (сообщения по порядку, секунд до первого изменения или None).
    """
    seen = {}
    start = time.time()
    last_change = start
    first = None
    while time.time() - start < total:
        msgs = list(client.iter_messages(entity, min_id=after_id, limit=30))
        snapshot = {m.id: (m.message, m.edit_date) for m in msgs}
        if snapshot != seen:
            if not seen and snapshot:
                first = round(time.time() - start, 1)
            seen = snapshot
            last_change = time.time()
        elif seen and time.time() - last_change >= quiet:
            break
        time.sleep(2)
    msgs = list(reversed(list(client.iter_messages(entity, min_id=after_id, limit=30))))
    return msgs, first


def build_parser():
    ap = argparse.ArgumentParser(description="Живой прогон бота в Telegram (agent-audit)")
    ap.add_argument("--bot", required=True, help="@username бота — единственный адресат")
    ap.add_argument("--transcript", help="JSONL-файл транскрипта (дописывается)")
    ap.add_argument("--session", help="файл сессии Telethon (иначе TG_SESSION или "
                    + DEFAULT_SESSION + ")")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read"); r.add_argument("n", nargs="?", type=int, default=10)
    for name in ("send", "click", "wait"):
        p = sub.add_parser(name)
        if name == "send":
            p.add_argument("text")
        elif name == "click":
            p.add_argument("msg_id", type=int); p.add_argument("button")
        else:
            p.add_argument("after_id", type=int)
        p.add_argument("--wait", type=int, default=20, help="секунд тишины после ответа")
        p.add_argument("--total", type=int, default=600, help="потолок ожидания, секунд")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    bot = a.bot.lstrip("@")
    with get_client(a.session) as client:
        entity = ensure_bot(client.get_entity(bot), a.bot)
        if a.cmd == "read":
            msgs = list(reversed(list(client.iter_messages(entity, limit=a.n))))
            show(msgs)
            return
        t0 = time.time()
        if a.cmd == "send":
            sent = client.send_message(entity, a.text)
            print(f"→ отправил [{sent.id}]: {a.text[:80]}")
            msgs, first = wait_new(client, entity, sent.id, a.wait, a.total)
            record = {"cmd": "send", "input": a.text, "sent_id": sent.id}
        elif a.cmd == "click":
            msg = client.get_messages(entity, ids=a.msg_id)
            if not msg or not msg.buttons:
                raise SystemExit("у сообщения нет кнопок")
            target = find_button(msg.buttons, a.button)
            if target is None:
                raise SystemExit("кнопка не найдена: " +
                                 " | ".join(b.text for row in msg.buttons for b in row))
            last = client.get_messages(entity, limit=1)[0].id
            res = msg.click(text=target.text)
            note = getattr(res, "message", "") or ""
            print(f"→ нажал «{target.text}» на [{msg.id}]; ответ: {note}")
            after = last - 1 if last > msg.id else msg.id
            msgs, first = wait_new(client, entity, after, a.wait, a.total)
            record = {"cmd": "click", "msg_id": msg.id, "button": target.text, "callback_answer": note}
        else:
            msgs, first = wait_new(client, entity, a.after_id, a.wait, a.total)
            record = {"cmd": "wait", "after_id": a.after_id}
        show(msgs)
        record.update({
            "bot": bot,
            "responses": [fmt(m) for m in msgs],
            "first_response_s": first,
            "waited_s": round(time.time() - t0, 1),
        })
        append_transcript(a.transcript, record)


if __name__ == "__main__":
    main()
