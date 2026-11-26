import tempfile
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

    def test_channel_post_requires_enrollment_and_caption(self):
        update = {"channel_post": {"chat": {"id": -300, "type": "channel", "title": "C"},
                                   "caption": "#слово", "photo": [{"file_id": "x"}]}}
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

    def test_migration_discards_bot_global_words_and_keeps_personal_words(self):
        import sqlite3
        legacy_path = Path(tempfile.mktemp(suffix=".sqlite3"))
        db = sqlite3.connect(legacy_path)
        db.executescript("""
            CREATE TABLE words(id INTEGER PRIMARY KEY, hanzi TEXT, pinyin TEXT, translation TEXT);
            INSERT INTO words(hanzi,pinyin,translation) VALUES ('旧','jiù','old shared');
            CREATE TABLE users(telegram_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,last_name TEXT,created_at INTEGER,last_seen INTEGER);
            INSERT INTO users VALUES (77,'u','User','',1,1);
            CREATE TABLE user_words(id INTEGER PRIMARY KEY,telegram_id INTEGER,hanzi TEXT,pinyin TEXT,translation TEXT,created_at INTEGER);
            INSERT INTO user_words VALUES (1,77,'你好','nǐ hǎo','hello',1);
        """)
        db.commit()
        db.close()
        migrated = Store(legacy_path)
        self.assertEqual(migrated.words(), [])
        personal = migrated.personal_dictionary(77)
        self.assertEqual([tuple(row)[0] for row in migrated.dictionary_words(personal)], ['你好'])
        with sqlite3.connect(legacy_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM words").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM dictionary_words WHERE hanzi='旧'").fetchone()[0], 0)
        legacy_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
