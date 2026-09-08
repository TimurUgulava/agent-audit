#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Онбординг Telegram-транспорта для живого прогона (agent-audit).

Запускай В СВОЁМ ТЕРМИНАЛЕ, не через ассистента: api_hash, код входа и пароль
двухфакторной защиты вводятся в скрытом поле и не должны попадать в переписку.

  setup_telegram.py                     мастер: учётные данные → вход → проверка
  setup_telegram.py --session ПУТЬ      подключить уже авторизованную сессию Telethon (без входа)
  setup_telegram.py --bot @имя          дополнительно проверить, что адресат — бот
  setup_telegram.py check [--bot @имя]  диагностика без изменений
  setup_telegram.py logout              выйти из сессии и удалить сохранённые данные

Что сохраняется: api_id и api_hash — в системное хранилище секретов (пакет keyring),
без него — в ~/.agent-audit/telegram.env с правами 0600; файл сессии — 0600.
Ничего не отправляется никуда, кроме серверов Telegram при входе.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _tg_auth as auth  # noqa: E402

INTRO = """\
Живой прогон пишет вашему боту от вашего аккаунта Telegram: бот не может нажимать
собственные кнопки, поэтому нужен настоящий пользователь. Понадобятся:
  1. api_id и api_hash вашего приложения — https://my.telegram.org → API development tools
     (название и короткое имя приложения любые, платформа Desktop).
  2. Телефон аккаунта и код входа, который придёт в Telegram; пароль двухфакторной защиты,
     если включена.
Правила, чтобы не получить ограничение аккаунта: только свой номер, одна сессия на все
прогоны, никаких рассылок и чужих чатов, человеческий темп. Подробно —
references/telegram-setup.md. Всё вводится здесь, в скрытых полях; ассистент этого не видит.
"""


def ask(prompt, secret=False):
    value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    if not value:
        raise SystemExit("пусто — прервано, ничего не сохранено")
    return value


def store_credentials(api_id, api_hash):
    if auth.keyring_set(auth.KEYRING_ID, api_id) and auth.keyring_set(auth.KEYRING_HASH, api_hash):
        return "системное хранилище секретов (keyring)"
    auth.write_env_file(auth.CONFIG_FILE, {"TG_API_ID": api_id, "TG_API_HASH": api_hash})
    return f"{auth.CONFIG_FILE} (права 0600; keyring не установлен: pip install keyring)"


def verify_bot(client, name):
    entity = client.get_entity(name.lstrip("@"))
    if not getattr(entity, "bot", False):
        raise SystemExit(f"«{name}» — не бот; живой прогон пишет только ботам")
    print(f"✓ {name}: бот, адресат разрешён")


def cmd_setup(a):
    print(INTRO)
    creds = auth.resolve_credentials()
    if creds["api_id"] and creds["api_hash"] and not a.reset:
        print(f"Учётные данные уже есть (api_id {auth.mask(creds['api_id'])}, "
              f"источник: {creds['sources']['api_id']}). Заменить — запусти с --reset.")
        api_id, api_hash = creds["api_id"], creds["api_hash"]
        if creds["sources"]["api_id"] == "окружение":
            print("Сохранено на будущее в:", store_credentials(api_id, api_hash))
    else:
        api_id = ask("api_id: ")
        if not api_id.isdigit():
            raise SystemExit("api_id — число")
        api_hash = ask("api_hash (ввод скрыт): ", secret=True)
        print("Сохранено в:", store_credentials(api_id, api_hash))
        creds = auth.resolve_credentials()
    session = a.session or creds["session"]
    if a.session:
        auth.write_env_file(auth.CONFIG_FILE, {"TG_SESSION": os.path.abspath(a.session)})
        print("Сессия подключена:", os.path.abspath(a.session))
    client = auth.make_client({"api_id": api_id, "api_hash": api_hash, "session": session})
    print("\nВход в Telegram. Telethon спросит телефон, затем код из приложения Telegram,"
          " затем пароль двухфакторной защиты, если она включена.")
    with client:
        me = client.get_me()
        print(f"✓ Вошли как {me.first_name or ''} (id {me.id}); устройство в списке сессий: «Agent Audit»")
        if a.bot:
            verify_bot(client, a.bot)
    auth.protect_session(session)
    print(f"✓ Сессия: {session} (0600). Больше входить не нужно — не запускай мастер повторно"
          " и не копируй файл сессии на другие машины.")
    print("Проверка в любой момент: python3 scripts/setup_telegram.py check")


def cmd_check(a):
    try:
        import telethon
        print("✓ telethon", telethon.__version__); telethon_ok = True
    except ImportError:
        print("✗ telethon не установлен: pip install telethon"); telethon_ok = False
    creds = auth.resolve_credentials()
    src = creds["sources"]
    print(f"{'✓' if creds['api_id'] else '✗'} api_id {auth.mask(creds['api_id'])} — {src['api_id'] or 'нет'}")
    print(f"{'✓' if creds['api_hash'] else '✗'} api_hash {auth.mask(creds['api_hash'])} — {src['api_hash'] or 'нет'}")
    session = a.session or creds["session"]
    exists = os.path.exists(session) or os.path.exists(session + ".session")
    print(f"{'✓' if exists else '✗'} сессия {session} — {src['session']}{'' if exists else ' (файла нет)'}")
    if not (telethon_ok and creds["api_id"] and creds["api_hash"] and exists):
        print("→ запусти мастер: python3 scripts/setup_telegram.py"); return 1
    client = auth.make_client(creds, session)
    client.connect()
    try:
        if not client.is_user_authorized():
            print("✗ сессия не авторизована — запусти мастер заново"); return 1
        me = client.get_me()
        print(f"✓ авторизован: {me.first_name or ''} (id {me.id})")
        if a.bot:
            verify_bot(client, a.bot)
    finally:
        client.disconnect()
    print("✓ транспорт готов к живому прогону")
    return 0


def cmd_logout(a):
    creds = auth.resolve_credentials()
    session = a.session or creds["session"]
    if input(f"Выйти из сессии {session} и удалить сохранённые учётные данные? [y/N] ").lower() != "y":
        print("отменено"); return 0
    if creds["api_id"] and creds["api_hash"] and (os.path.exists(session) or os.path.exists(session + ".session")):
        client = auth.make_client(creds, session)
        client.connect()
        try:
            if client.is_user_authorized():
                client.log_out()
                print("✓ сессия завершена на стороне Telegram")
        finally:
            client.disconnect()
    for path in (session, session + ".session", session + ".session-journal"):
        if os.path.exists(path):
            os.remove(path); print("удалён", path)
    auth.keyring_delete(auth.KEYRING_ID); auth.keyring_delete(auth.KEYRING_HASH)
    if os.path.exists(auth.CONFIG_FILE):
        os.remove(auth.CONFIG_FILE); print("удалён", auth.CONFIG_FILE)
    print("✓ готово")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="Онбординг Telegram для живого прогона (agent-audit)")
    ap.add_argument("cmd", nargs="?", choices=["setup", "check", "logout"], default="setup")
    ap.add_argument("--session", help="путь к файлу сессии Telethon (существующей или новой)")
    ap.add_argument("--bot", help="@имя бота для проверки адресата")
    ap.add_argument("--reset", action="store_true", help="заменить сохранённые api_id/api_hash")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == "setup":
        return cmd_setup(a) or 0
    if a.cmd == "check":
        return cmd_check(a)
    return cmd_logout(a)


if __name__ == "__main__":
    sys.exit(main())
