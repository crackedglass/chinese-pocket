import tempfile
import subprocess
import stat
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT.parent))
from chinese_bot.__main__ import Bot
from chinese_bot.store import Store, parse_words
from chinese_bot.telegram import TelegramError


class FakeApi:
    def __init__(self):
        self.sent = []

    def call(self, method, **payload):
        if method == "sendMessage":
            self.sent.append(payload)
        if method == "getMe":
            return {"id": 999}
        if method == "getChat":
            return {"id": payload["chat_id"], "title": "Test"}
        return True

    def photo(self, file_id):
        return b"image"

    def voice(self, file_id):
        return b"voice"

    def is_channel_admin(self, channel_id, user_id):
        return user_id in {7, 8, 999}


class BotRegressionTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mktemp(suffix=".sqlite3"))
        self.env = patch.dict("os.environ", {"BOT_DB_PATH": str(self.path), "TELEGRAM_BOT_TOKEN": "1:test", "APP_SECRET": "secret"})
        self.env.start()
        self.store = Store(self.path)
        self.api = FakeApi()
        self.bot = Bot(self.api, self.store, ocr=lambda _: "你好 | nǐ hǎo | привет")

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        self.env.stop()

    def test_private_start_and_confirmed_image_import(self):
        self.bot.handle({"chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                         "text": "/start"})
        self.assertIn("#слово", self.api.sent[-1]["text"])
        self.bot.handle({"chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                         "caption": "#слово", "photo": [{"file_id": "x"}]})
        preview = self.api.sent[-1]["text"]
        token = preview.split("/confirm ", 1)[1].split()[0]
        personal = self.store.personal_dictionary(7)
        self.assertEqual(self.store.dictionary_words(personal), [])
        self.bot.handle({"chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                         "text": "/confirm " + token})
        self.assertEqual(len(self.store.dictionary_words(personal)), 1)
        self.assertIn("Импортировано слов: 1", self.api.sent[-1]["text"])

    def test_channel_scope_isolated_and_disabled_ignored(self):
        message = {"chat": {"id": -100, "type": "channel", "title": "Test"},
                   "caption": "#слово", "photo": [{"file_id": "x"}]}
        self.bot.handle(message)
        self.assertEqual(self.store.channel_words(-100), [])
        dictionary_id = self.store.create_dictionary(7, "Test dictionary")
        self.store.link_channel(-100, "Test", dictionary_id)
        self.bot.handle(message)
        self.assertEqual(self.store.dictionary_words(dictionary_id), [])
        self.assertIn("/confirm", self.api.sent[-1]["text"])

    def test_only_channel_admin_can_toggle_channel(self):
        self.bot.handle({"chat": {"id": 8, "type": "private"}, "from": {"id": 8},
                         "text": "/channel_on -100"})
        self.assertIsNone(self.store.channel(-100))
        dictionary_id = self.store.create_dictionary(7, "Test dictionary")
        self.bot.handle({"chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                         "text": f"/channel_on -100 {dictionary_id}"})
        self.assertTrue(self.store.channel(-100)["enabled"])

    def test_store_confirmation_is_idempotent(self):
        words = parse_words("学校 | xuéxiào | школа")
        dictionary_id = self.store.create_dictionary(7, "Import dictionary")
        self.store.link_channel(-100, "Test", dictionary_id)
        self.store.create_import("channel", -100, words, "token")
        self.assertEqual(self.store.confirm_import("token", "channel", -100), 1)
        self.assertEqual(self.store.confirm_import("token", "channel", -100), 0)
        self.assertEqual(len(self.store.dictionary_words(dictionary_id)), 1)

    def test_expired_and_unauthorized_import_confirmation_persists_nothing(self):
        words = parse_words("书 | shū | книга")
        dictionary_id = self.store.personal_dictionary(51)
        self.store.create_import("user", 51, words, "expired", dictionary_id=dictionary_id)
        with self.store.connect() as db:
            db.execute("UPDATE imports SET created_at=0 WHERE token='expired'")
        self.assertEqual(self.store.confirm_import("expired", "user", 51), 0)
        self.store.create_import("user", 52, words, "private")
        self.assertEqual(self.store.confirm_import("private", "user", 53), 0)
        self.assertEqual(self.store.dictionary_words(dictionary_id), [])

    def test_channel_review_does_not_leak_global_words(self):
        self.store.add([("猫", "māo", "кот")])
        dictionary_id = self.store.create_dictionary(7, "Channel dictionary")
        self.store.link_channel(-100, "Test", dictionary_id)
        self.store.add_dictionary_words(dictionary_id, [("狗", "gǒu", "собака")])
        self.bot.review(datetime(2026, 1, 1, 12, tzinfo=UTC))
        self.assertEqual(len(self.api.sent), 1)
        self.assertIn("狗", self.api.sent[0]["text"])
        self.assertNotIn("猫", self.api.sent[0]["text"])
        self.assertEqual(self.store.channel_due("2026-01-01"), [])

    def test_add_remove_channel_admin_persists_only_enabled_after_explicit_setup(self):
        self.bot.process_update({"my_chat_member": {"chat": {"id": -200, "type": "channel", "title": "C"},
                                 "new_chat_member": {"status": "administrator"}}})
        self.assertFalse(self.store.channel(-200)["enabled"])
        dictionary_id = self.store.create_dictionary(7, "Test dictionary")
        self.bot.handle({"chat": {"id": 7, "type": "private"}, "from": {"id": 7},
                         "text": f"/channel_on -200 {dictionary_id}"})
        self.assertTrue(self.store.channel(-200)["enabled"])
        self.bot.process_update({"my_chat_member": {"chat": {"id": -200, "type": "channel", "title": "C"},
                                 "new_chat_member": {"status": "left"}}})
        self.assertFalse(self.store.channel(-200)["enabled"])

    def test_image_import_recognizes_two_real_fixture_rows_and_creates_preview(self):
        fixture = Path(__file__).parent / "fixtures" / "vocabulary-2-rows.png"
        ocr_output = subprocess.run(["docker", "run", "--rm", "-v", f"{fixture}:/tmp/vocab.png",
                                     "chinese-pocket-cjk-test", "tesseract", "/tmp/vocab.png",
                                     "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                                    stdout=subprocess.PIPE, check=True).stdout
        recognized = ocr_output.decode().strip()
        rows = parse_words("\n".join(" | ".join(part.strip() for part in line.split("|"))
                                 for line in recognized.splitlines()))
        self.assertEqual(len(rows), 2)
        self.bot.ocr = lambda _image: recognized
        self.bot.handle({"chat": {"id": 51, "type": "private"}, "from": {"id": 51},
                         "caption": "#слово", "photo": [{"file_id": "fixture"}]})
        self.assertIn("/confirm", self.api.sent[-1]["text"])
        dictionary_id = self.store.personal_dictionary(51)
        self.assertEqual(self.store.dictionary_words(dictionary_id), [])

    def test_channel_post_requires_enrollment_and_caption(self):
        update = {"channel_post": {"chat": {"id": -300, "type": "channel", "title": "C"},
                                   "caption": "#слово", "photo": [{"file_id": "x"}]}}
        self.bot.process_update({"my_chat_member": {"chat": {"id": -300, "type": "channel", "title": "C"}, "new_chat_member": {"status": "administrator"}}})
        self.assertFalse(self.store.channel(-300)["enabled"])
        self.bot.process_update(update)
        self.assertEqual(self.api.sent, [])
        dictionary_id = self.store.create_dictionary(7, "Channel dictionary")
        self.store.link_channel(-300, "C", dictionary_id)
        self.bot.process_update(update)
        self.assertIn("/confirm", self.api.sent[-1]["text"])
        self.assertEqual(self.store.dictionary_words(dictionary_id), [])
        self.bot.process_update({"channel_post": {"chat": {"id": -300, "type": "channel"},
                                 "caption": "ordinary photo", "photo": [{"file_id": "x"}]}})
        self.assertEqual(len(self.api.sent), 1)

    def test_image_import_rejects_empty_and_malformed_ocr_without_saving(self):
        for result in ("", "not a vocabulary row", "你好 | missing translation"):
            self.bot.ocr = lambda _image, result=result: result
            before = len(self.api.sent)
            self.bot.handle({"chat": {"id": 41, "type": "private"}, "from": {"id": 41},
                             "caption": "#слово", "photo": [{"file_id": "x"}]})
            self.assertEqual(len(self.api.sent), before + 1)
            self.assertNotIn("/confirm", self.api.sent[-1]["text"])
            dictionary_id = self.store.personal_dictionary(41)
            self.assertEqual(self.store.dictionary_words(dictionary_id), [])

    def test_image_import_reports_ocr_failure_without_saving(self):
        def fail(_image):
            raise OSError("ocr unavailable")
        self.bot.ocr = fail
        self.bot.handle({"chat": {"id": 42, "type": "private"}, "from": {"id": 42},
                         "caption": "#слово", "photo": [{"file_id": "x"}]})
        self.assertIn("Не удалось распознать фото", self.api.sent[-1]["text"])
        self.assertEqual(self.store.dictionary_words(self.store.personal_dictionary(42)), [])

    def test_oversized_photo_is_rejected_without_saving(self):
        class LargePhotoApi(FakeApi):
            def photo(self, file_id):
                raise TelegramError(400)
        api = LargePhotoApi()
        bot = Bot(api, self.store)
        bot.handle({"chat": {"id": 43, "type": "private"}, "from": {"id": 43},
                    "caption": "#слово", "photo": [{"file_id": "x"}]})
        self.assertIn("Не удалось распознать фото", api.sent[-1]["text"])
        self.assertEqual(self.store.dictionary_words(self.store.personal_dictionary(43)), [])

    def test_migration_discards_bot_global_words_and_keeps_personal_words(self):
        import sqlite3
        legacy_path = Path(tempfile.mktemp(suffix=".sqlite3"))
        db = sqlite3.connect(legacy_path)
        db.executescript("""
            CREATE TABLE words(id INTEGER PRIMARY KEY, hanzi TEXT, pinyin TEXT, translation TEXT);
            INSERT INTO words(hanzi,pinyin,translation) VALUES ('旧','jiù','old shared');
            CREATE TABLE dictionaries(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,owner_id INTEGER,created_at INTEGER);
            INSERT INTO dictionaries VALUES (99,'Legacy','legacy',NULL,1);
            CREATE TABLE dictionary_members(dictionary_id INTEGER,telegram_id INTEGER,role TEXT,joined_at INTEGER);
            CREATE TABLE dictionary_words(id INTEGER PRIMARY KEY,dictionary_id INTEGER,hanzi TEXT,pinyin TEXT,translation TEXT,created_at INTEGER);
            INSERT INTO dictionary_words VALUES (1,99,'舊','jiù','legacy term',1);
            CREATE TABLE users(telegram_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,last_name TEXT,created_at INTEGER,last_seen INTEGER);
            INSERT INTO users VALUES (77,'u','User','',1,1);
            CREATE TABLE user_words(id INTEGER PRIMARY KEY,telegram_id INTEGER,hanzi TEXT,pinyin TEXT,translation TEXT,created_at INTEGER);
            INSERT INTO user_words VALUES (1,77,'你好','nǐ hǎo','hello',1);
        """)
        db.commit()
        db.close()
        migrated = Store(legacy_path)
        backup_path = Path(str(legacy_path) + ".pre-dictionary-v1.sqlite3")
        self.assertTrue(backup_path.exists())
        self.assertEqual(stat.S_IMODE(backup_path.stat().st_mode), 0o600)
        with sqlite3.connect(backup_path) as backup:
            self.assertEqual(backup.execute("SELECT count(*) FROM words").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT count(*) FROM dictionary_words WHERE dictionary_id=99").fetchone()[0], 1)
        self.assertEqual(migrated.words(), [])
        personal = migrated.personal_dictionary(77)
        self.assertEqual([tuple(row)[0] for row in migrated.dictionary_words(personal)], ['你好'])
        with sqlite3.connect(legacy_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM words").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM dictionary_words WHERE hanzi IN ('旧','舊')").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM dictionaries WHERE kind='legacy'").fetchone()[0], 0)
        with sqlite3.connect(backup_path) as backup:
            self.assertEqual(backup.execute("SELECT count(*) FROM words").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT count(*) FROM dictionary_words WHERE dictionary_id=99").fetchone()[0], 1)
        legacy_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
