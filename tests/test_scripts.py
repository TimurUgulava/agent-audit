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
        import os
        saved = {k: os.environ.pop(k, None) for k in ('TG_API_ID', 'TG_API_HASH')}
        try:
            with self.assertRaises(SystemExit) as ctx:
                self.drive.get_client()
            self.assertIn('TG_API_ID', str(ctx.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_bot_flag_required_without_telethon(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'tg_drive.py'), 'read'],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--bot', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
