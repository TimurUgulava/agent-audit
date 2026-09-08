#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живой прогон агента в Telegram от аккаунта владельца (agent-audit, live-drive.md).

Пишет ТОЛЬКО названному боту: сущность без признака bot отвергается, людям и чатам
писать нельзя. Авторизация — согласованный план прогона текущей ревизии.
Транспорт настраивается мастером scripts/setup_telegram.py (references/telegram-setup.md):
api_id/api_hash — окружение TG_API_ID/TG_API_HASH, keyring или ~/.agent-audit/telegram.env;
сессия — TG_SESSION, тот же файл или ~/.agent-audit/tg-drive.session. Секреты не печатаются.

  tg_drive.py --bot @имя read [N]
  tg_drive.py --bot @имя send "<текст>" [--wait S] [--total S]
  tg_drive.py --bot @имя click <msg_id> "<кнопка>" [--wait S] [--total S]
  tg_drive.py --bot @имя wait <after_id> [--wait S] [--total S]
Общие флаги: --transcript файл.jsonl (дописывать запись каждого шага — источник live),
--session путь (файл сессии Telethon).

Против ограничения аккаунта: пауза не меньше двух секунд между действиями даже из разных
процессов, опрос чата раз в 3–6 секунд, FloodWait до двух минут выдерживается, дольше —
остановка без повторов. Rich-сообщения бота Telethon не разбирает: полный текст читай в
точке чтения из карты. Ожидание: --wait секунд тишины, но не дольше --total.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _tg_auth as auth  # noqa: E402

POLL_FAST, POLL_SLOW, SLOW_AFTER = 3, 6, 60


def get_client(session=None, env=None, config_path=auth.CONFIG_FILE, use_keyring=True):
    """Клиент по учётным данным из окружения/keyring/файла; без них — понятный отказ."""
    creds = auth.resolve_credentials(env=env, config_path=config_path, use_keyring=use_keyring)
    return auth.make_client(creds, session)


def with_flood_guard(action, what):
    """Выполнить действие; FloodWait до лимита выждать один раз, дольше — остановиться."""
    try:
        return action()
    except Exception as exc:  # FloodWaitError импортировать без Telethon нельзя
        seconds = auth.flood_wait_seconds(exc)
        if seconds is None:
            raise
        if seconds > auth.FLOOD_WAIT_CAP:
            raise SystemExit(f"Telegram просит подождать {seconds} с перед «{what}» — прогон "
                             "остановлен; не повторяй команду, отметь в транскрипте")
        print(f"… Telegram просит подождать {seconds} с — жду")
        time.sleep(seconds + 1)
        return action()


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
        msgs = with_flood_guard(lambda: list(client.iter_messages(entity, min_id=after_id, limit=30)), "чтение")
        snapshot = {m.id: (m.message, m.edit_date) for m in msgs}
        if snapshot != seen:
            if not seen and snapshot:
                first = round(time.time() - start, 1)
            seen = snapshot
            last_change = time.time()
        elif seen and time.time() - last_change >= quiet:
            break
        time.sleep(POLL_SLOW if time.time() - start > SLOW_AFTER else POLL_FAST)
    msgs = list(reversed(list(client.iter_messages(entity, min_id=after_id, limit=30))))
    return msgs, first


def build_parser():
    # --transcript и --session принимаются и до, и после подкоманды
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--transcript", default=argparse.SUPPRESS,
                        help="JSONL-файл транскрипта (дописывается)")
    common.add_argument("--session", default=argparse.SUPPRESS,
                        help="файл сессии Telethon (иначе TG_SESSION, файл настроек или "
                        + auth.DEFAULT_SESSION + ")")
    ap = argparse.ArgumentParser(description="Живой прогон бота в Telegram (agent-audit)",
                                 parents=[common])
    ap.add_argument("--bot", required=True, help="@username бота — единственный адресат")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", parents=[common]); r.add_argument("n", nargs="?", type=int, default=10)
    for name in ("send", "click", "wait"):
        p = sub.add_parser(name, parents=[common])
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
    transcript = getattr(a, "transcript", None)
    client = get_client(getattr(a, "session", None))
    client.connect()   # без start(): вход не должен идти через ассистента
    try:
        if not client.is_user_authorized():
            raise SystemExit("сессия не авторизована — выполни в своём терминале "
                             "`python3 scripts/setup_telegram.py` (references/telegram-setup.md)")
        entity = ensure_bot(client.get_entity(bot), a.bot)
        if a.cmd == "read":
            msgs = list(reversed(list(client.iter_messages(entity, limit=a.n))))
            show(msgs)
            return
        auth.pace()
        t0 = time.time()
        if a.cmd == "send":
            sent = with_flood_guard(lambda: client.send_message(entity, a.text), "отправка")
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
            res = with_flood_guard(lambda: msg.click(text=target.text), "нажатие")
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
        append_transcript(transcript, record)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
