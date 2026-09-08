"""Regression checks use isolated projects; no bot/network calls."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'


class ScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def run_script(self, name, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / name), str(self.root), *args],
                                text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
        return result.stdout + result.stderr

    def ledger(self, *args, **kwargs):
        return self.run_script('ledger.py', *args, **kwargs)

    def extract(self, source, *args):
        (self.root / 'bot.py').write_text(source)
        return self.run_script('extract_promises.py', *args)

    def test_latest_revision(self):
        self.ledger('stamp', '2026-09-01', 'old')
        self.ledger('stamp', '2026-09-05', 'new')
        self.assertIn('последняя: 2026-09-05 коммит new', self.ledger('status'))

    def test_same_day_revisions_unique_reports_and_newest(self):
        self.ledger('stamp', '2026-09-05', 'old')
        self.ledger('stamp', '2026-09-05', 'new')
        self.assertIn('2026-09-05-2.md', (self.root / 'docs/audits/ledger.md').read_text())
        self.assertIn('коммит new', self.ledger('status'))

    def test_stamp_idempotency_and_explicit_report(self):
        self.ledger('stamp', '2026-09-05', 'abc', '--report', 'custom.md')
        self.ledger('stamp', '2026-09-05', 'abc', '--report', 'custom.md')
        self.assertIn('ревизий: 1;', self.ledger('status'))
        self.ledger('stamp', '2026-09-05', 'def', '--report', 'custom.md', success=False)

    def test_regressions_count_as_open_and_ids_never_reused(self):
        self.ledger('stamp', '2026-09-05', 'abc')
        path = self.root / 'docs/audits/ledger.md'
        with path.open('a') as stream:
            stream.write('| AA-001 | fp | 1 | P1 | регресс | now | now | | Regressed |\n')
            stream.write('| AA-010 | fp2 | 1 | P2 | закрыто | now | now | now | Closed |\n')
        self.assertIn('открытых находок: 1;', self.ledger('status'))
        self.assertIn('Regressed', self.ledger('open'))
        self.assertEqual('AA-011\n', self.ledger('next'))

    def test_missing_stamp_arguments(self):
        self.ledger('stamp', success=False)

    def test_invalid_calendar_date_rejected(self):
        self.ledger('stamp', '2026-02-31', 'abc', success=False)
        self.assertFalse((self.root / 'docs/audits/ledger.md').exists())

    def test_send_message_uses_second_argument_and_short_english(self):
        out = self.extract('bot.send_message(12345, "OK")\nbot.send_message(chat_id=1, text="Hi")\nm.answer("Go")')
        self.assertIn('bot.py:1  OK', out)
        self.assertIn('bot.py:2  Hi', out)
        self.assertIn('bot.py:3  Go', out)
        self.assertIn('НЕ СМОГ РАЗРЕШИТЬ: 0', out)

    def test_media_files_not_extracted_as_text(self):
        out = self.extract('m.answer_photo("file-id")\nbot.send_document(1, "private.pdf")\nbot.send_photo(1, "id", caption="Done")')
        self.assertNotIn('private.pdf', out)
        self.assertNotIn('file-id', out)
        self.assertIn('bot.py:3  Done', out)

    def test_ambiguous_media_and_wrappers_reported(self):
        out = self.extract('m.answer_photo("id", "maybe caption")\nbot.send_long(1, "Hi")\nbot.send_message(**kwargs)')
        self.assertIn('НЕ СМОГ РАЗРЕШИТЬ: 3', out)
        self.assertIn('неизвестная сигнатура', out)

    def test_dynamic_text_reported(self):
        out = self.extract('bot.send_message(1, make_text())')
        self.assertIn('динамический/неподдерживаемый текст: make_text()', out)

    def test_full_output_preserves_end_of_long_promises(self):
        source = 'm.answer(' + repr('начало ' * 100 + '\nTAIL-PROMISE') + ')'
        self.assertNotIn('TAIL-PROMISE', self.extract(source))
        self.assertIn('TAIL-PROMISE', self.extract(source, '--full', '--md'))

    def test_coverage_label_does_not_claim_full_promise_coverage(self):
        out = self.extract('m.answer("OK")')
        self.assertIn('НЕ покрытие всех обещаний', out)
        self.assertNotIn('покрытие статикой', out)

    def test_parse_failure_reported(self):
        self.assertIn('SyntaxError', self.extract('def broken('))


class DriveTest(unittest.TestCase):
    """Чистые функции tg_drive.py без Telethon и сети."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('tg_drive', SCRIPTS / 'tg_drive.py')
        cls.drive = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.drive)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    @staticmethod
    def button(text):
        return type('B', (), {'text': text})()

    def test_find_button_substring_case_insensitive(self):
        rows = [[self.button('➕ Новая работа')], [self.button('✔️ Готово'), self.button('Отмена')]]
        self.assertEqual(self.drive.find_button(rows, 'готово').text, '✔️ Готово')
        self.assertIsNone(self.drive.find_button(rows, 'Опубликовать'))
        self.assertIsNone(self.drive.find_button(None, 'x'))

    def test_ensure_bot_refuses_people_and_chats(self):
        human = type('U', (), {'bot': False})()
        with self.assertRaises(SystemExit):
            self.drive.ensure_bot(human, '@someone')
        bot = type('U', (), {'bot': True})()
        self.assertIs(self.drive.ensure_bot(bot, '@bot'), bot)

    def test_fmt_marks_bot_media_and_buttons(self):
        import datetime
        msg = type('M', (), {})()
        msg.id = 7; msg.message = ''; msg.media = object(); msg.edit_date = None
        msg.date = datetime.datetime(2026, 9, 7, 14, 5, 9)
        msg.sender = type('S', (), {'bot': True})()
        msg.buttons = [[self.button('Пиши')]]
        d = self.drive.fmt(msg)
        self.assertEqual(d['who'], 'бот')
        self.assertEqual(d['text'], '<медиа>')
        self.assertEqual(d['buttons'], [['Пиши']])
        self.assertEqual(d['date'], '14:05:09')
        self.assertFalse(d['edited'])

    def test_transcript_appends_jsonl_with_timestamp(self):
        import json
        path = self.root / 'live.jsonl'
        self.drive.append_transcript(str(path), {'cmd': 'send', 'input': 'привет'})
        self.drive.append_transcript(str(path), {'cmd': 'wait'})
        self.drive.append_transcript(None, {'cmd': 'ignored'})
        lines = path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first['input'], 'привет')
        self.assertIn('ts', first)

    def test_client_without_credentials_fails_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            self.drive.get_client(env={}, config_path=str(self.root / 'none.env'), use_keyring=False)
        self.assertIn('setup_telegram.py', str(ctx.exception))

    def test_flood_guard_waits_short_and_stops_on_long(self):
        class Flood(Exception):
            def __init__(self, seconds):
                self.seconds = seconds
        calls = []
        def short():
            calls.append(1)
            if len(calls) == 1:
                raise Flood(0)
            return 'ok'
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.drive.with_flood_guard(short, 'x'), 'ok')
        with self.assertRaises(SystemExit):
            self.drive.with_flood_guard(lambda: (_ for _ in ()).throw(Flood(999)), 'x')
        with self.assertRaises(ValueError):
            self.drive.with_flood_guard(lambda: (_ for _ in ()).throw(ValueError('boom')), 'x')

    def test_transcript_and_session_accepted_after_subcommand(self):
        a = self.drive.build_parser().parse_args(
            ['--bot', '@b', 'send', 'привет', '--wait', '5', '--transcript', 't.jsonl', '--session', 's'])
        self.assertEqual(a.transcript, 't.jsonl')
        self.assertEqual(a.session, 's')
        b = self.drive.build_parser().parse_args(['--transcript', 'x.jsonl', '--bot', '@b', 'read'])
        self.assertEqual(b.transcript, 'x.jsonl')
        self.assertIsNone(getattr(b, 'session', None))
        c = self.drive.build_parser().parse_args(['--bot', '@b', 'wait', '10'])
        self.assertIsNone(getattr(c, 'transcript', None))

    def test_bot_flag_required_without_telethon(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'tg_drive.py'), 'read'],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--bot', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class AuthTest(unittest.TestCase):
    """Учётные данные Telegram: порядок источников, права файла, маска, мастер без секретов."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('_tg_auth', SCRIPTS / '_tg_auth.py')
        cls.auth = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.auth)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_env_file_written_private_and_read_back(self):
        import os, stat
        path = self.root / 'telegram.env'
        self.auth.write_env_file(str(path), {'TG_API_ID': '123', 'TG_API_HASH': 'abcdef0123'})
        self.auth.write_env_file(str(path), {'TG_SESSION': '/tmp/s.session'})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        values = self.auth.read_env_file(str(path))
        self.assertEqual(values['TG_API_ID'], '123')
        self.assertEqual(values['TG_API_HASH'], 'abcdef0123')
        self.assertEqual(values['TG_SESSION'], '/tmp/s.session')

    def test_resolution_order_env_over_file(self):
        path = self.root / 'telegram.env'
        self.auth.write_env_file(str(path), {'TG_API_ID': '1', 'TG_API_HASH': 'file-hash'})
        creds = self.auth.resolve_credentials(env={'TG_API_ID': '2'}, config_path=str(path), use_keyring=False)
        self.assertEqual(creds['api_id'], '2')
        self.assertEqual(creds['sources']['api_id'], 'окружение')
        self.assertEqual(creds['api_hash'], 'file-hash')
        self.assertEqual(creds['sources']['api_hash'], str(path))
        self.assertEqual(creds['session'], self.auth.DEFAULT_SESSION)
        empty = self.auth.resolve_credentials(env={}, config_path=str(self.root / 'no.env'), use_keyring=False)
        self.assertIsNone(empty['api_id'])

    def test_mask_never_reveals_secret(self):
        self.assertEqual(self.auth.mask(None), '—')
        self.assertNotIn('abcdef', self.auth.mask('abcdef0123456789'))
        self.assertEqual(self.auth.mask('123'), '•••')

    def test_setup_help_and_check_without_credentials(self):
        import os
        env = {k: v for k, v in os.environ.items() if not k.startswith('TG_')}
        env['AGENT_AUDIT_HOME'] = str(self.root)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'setup_telegram.py'), '--help'],
                                text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('setup', result.stdout)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'setup_telegram.py'), 'check'],
                                text=True, capture_output=True, env=env)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('api_id', result.stdout)
        self.assertNotIn('TG_API_HASH=', result.stdout)


if __name__ == '__main__':
    unittest.main()
