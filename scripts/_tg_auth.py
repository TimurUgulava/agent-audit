#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Учётные данные и клиент Telethon для живого прогона (agent-audit).

Порядок поиска api_id / api_hash: переменные окружения TG_API_ID и TG_API_HASH →
системное хранилище секретов (пакет keyring, сервис «agent-audit») → файл
~/.agent-audit/telegram.env с правами 0600. Путь к сессии: TG_SESSION → тот же файл →
~/.agent-audit/tg-drive.session. Секреты никогда не печатаются целиком — только маска.
"""
import os
import stat

CONFIG_DIR = os.environ.get("AGENT_AUDIT_HOME") or os.path.expanduser("~/.agent-audit")
CONFIG_FILE = os.path.join(CONFIG_DIR, "telegram.env")
DEFAULT_SESSION = os.path.join(CONFIG_DIR, "tg-drive.session")
KEYRING_SERVICE = "agent-audit"
KEYRING_ID = "tg-api-id"
KEYRING_HASH = "tg-api-hash"
# Постоянное имя устройства: Telegram видит один и тот же «клиент», а не новый каждый раз.
DEVICE = {"device_model": "Agent Audit", "system_version": "live-drive", "app_version": "2.2"}
MIN_GAP_SECONDS = 2.0        # человеческий темп: не быстрее одного действия в 2 секунды
FLOOD_WAIT_CAP = 120         # FloodWait дольше — остановиться, не повторять


def read_env_file(path):
    """KEY=VALUE построчно; комментарии и пустые строки пропускаются."""
    values = {}
    if not path or not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_file(path, values):
    """Записать KEY=VALUE; каталог 0700, файл 0600 — читает только владелец."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    try:
        os.chmod(directory, stat.S_IRWXU)
    except OSError:
        pass
    existing = read_env_file(path)
    existing.update({k: v for k, v in values.items() if v is not None})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# agent-audit: учётные данные живого прогона Telegram. Не коммитить, не пересылать.\n")
        for key, value in existing.items():
            fh.write(f"{key}={value}\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def keyring_get(name):
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def keyring_set(name, value):
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, name, value)
        return True
    except Exception:
        return False


def keyring_delete(name):
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, name)
        return True
    except Exception:
        return False


def mask(value):
    """Маска секрета для вывода: первые и последние два знака."""
    if not value:
        return "—"
    value = str(value)
    if len(value) <= 6:
        return "•" * len(value)
    return value[:2] + "…" + value[-2:]


def resolve_credentials(env=None, config_path=CONFIG_FILE, use_keyring=True):
    """Найти api_id, api_hash и путь сессии; вернуть их и источник каждого."""
    env = os.environ if env is None else env
    file_values = read_env_file(config_path)

    def pick(env_key, keyring_name):
        if env.get(env_key):
            return env[env_key], "окружение"
        if use_keyring:
            value = keyring_get(keyring_name)
            if value:
                return value, "keyring"
        if file_values.get(env_key):
            return file_values[env_key], config_path
        return None, None

    api_id, id_src = pick("TG_API_ID", KEYRING_ID)
    api_hash, hash_src = pick("TG_API_HASH", KEYRING_HASH)
    session = env.get("TG_SESSION") or file_values.get("TG_SESSION") or DEFAULT_SESSION
    session_src = "окружение" if env.get("TG_SESSION") else (config_path if file_values.get("TG_SESSION") else "по умолчанию")
    return {"api_id": api_id, "api_hash": api_hash, "session": session,
            "sources": {"api_id": id_src, "api_hash": hash_src, "session": session_src}}


def missing_message():
    return ("нет учётных данных Telegram: выполни в своём терминале "
            "`python3 scripts/setup_telegram.py` (онбординг по references/telegram-setup.md) "
            "или задай TG_API_ID, TG_API_HASH и TG_SESSION в окружении")


def make_client(creds, session=None):
    """TelegramClient с постоянным именем устройства; без Telethon — понятный отказ."""
    if not creds.get("api_id") or not creds.get("api_hash"):
        raise SystemExit(missing_message())
    try:
        api_id = int(creds["api_id"])
    except (TypeError, ValueError):
        raise SystemExit("TG_API_ID должен быть числом из my.telegram.org")
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        raise SystemExit("нужен Telethon >= 1.44: pip install telethon")
    session = session or creds["session"]
    os.makedirs(os.path.dirname(os.path.abspath(session)), exist_ok=True)
    return TelegramClient(session, api_id, creds["api_hash"], **DEVICE)


def protect_session(session):
    """Файл сессии — только владельцу."""
    for path in (session, session + ".session"):
        if os.path.exists(path):
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass


def flood_wait_seconds(exc):
    """Секунды ожидания из FloodWaitError Telethon или None."""
    return getattr(exc, "seconds", None)


def pace(state_path=None, min_gap=MIN_GAP_SECONDS):
    """Выдержать паузу между действиями даже из разных процессов (файл с меткой времени)."""
    import time
    state_path = state_path or os.path.join(CONFIG_DIR, "last-action")
    try:
        last = float(open(state_path).read().strip())
        gap = time.time() - last
        if gap < min_gap:
            time.sleep(min_gap - gap)
    except (OSError, ValueError):
        pass
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass
