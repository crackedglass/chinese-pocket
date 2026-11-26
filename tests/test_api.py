import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient

from chinese_bot import app as appmod
from chinese_bot.store import Store


class DictionaryApiTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mktemp(suffix=".sqlite3"))
        self.old_path, self.old_secret, self.old_token = appmod.DB_PATH, appmod.APP_SECRET, appmod.BOT_TOKEN
        appmod.DB_PATH = str(self.path)
        appmod.APP_SECRET = "api-test-secret"
        appmod.BOT_TOKEN = "1:test"
        appmod.db().close()
        self.owner = self.client(7001)
        self.member = self.client(7002)

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        appmod.DB_PATH, appmod.APP_SECRET, appmod.BOT_TOKEN = self.old_path, self.old_secret, self.old_token

    def client(self, user_id):
        now = int(time.time())
        with appmod.db() as conn:
            conn.execute("INSERT OR IGNORE INTO users(telegram_id,username,first_name,last_name,created_at,last_seen) VALUES (?,?,?,?,?,?)",
                         (user_id, "user", "Test", "", now, now))
        Store(str(self.path)).personal_dictionary(user_id)
        client = TestClient(appmod.app)
        client.cookies.set("session", appmod.make_session({"id": user_id, "first_name": "Test", "username": f"tester{user_id}"}))
        return client

    def test_invite_dictionary_words_and_owner_permissions(self):
        created = self.owner.post("/api/dictionaries", json={"name": "Study group"})
        self.assertEqual(created.status_code, 200)
        dictionary_id = created.json()["dictionary_id"]
        invite = self.owner.post("/api/dictionaries/invite", json={"dictionary_id": dictionary_id})
        self.assertEqual(invite.status_code, 200)
        code = invite.json()["code"]
        self.assertEqual(self.member.post("/api/dictionaries/redeem", json={"code": code}).status_code, 200)
        self.assertEqual(self.member.post("/api/dictionaries/redeem", json={"code": code}).status_code, 400)
        self.assertEqual(self.member.post("/api/dictionaries/select", json={"dictionary_id": dictionary_id}).status_code, 200)
        added = self.member.post("/api/words", json={"hanzi": "你好", "pinyin": "nǐ hǎo", "translation": "привет"})
        self.assertEqual(added.status_code, 200)
        self.assertEqual(len(self.owner.get("/api/words").json()["words"]), 0)
        self.assertEqual(self.owner.post("/api/dictionaries/select", json={"dictionary_id": dictionary_id}).status_code, 200)
        self.assertEqual(len(self.owner.get("/api/words").json()["words"]), 1)
        self.assertEqual(self.member.request("DELETE", "/api/dictionaries/members", json={"dictionary_id": dictionary_id, "telegram_id": 7001}).status_code, 403)
        self.assertEqual(self.owner.request("DELETE", "/api/dictionaries/members", json={"dictionary_id": dictionary_id, "telegram_id": 7002}).status_code, 200)
        self.assertEqual(self.member.get("/api/words").status_code, 403)

    def test_channel_link_requires_dictionary_owner_and_channel_admin(self):
        dictionary = self.owner.post("/api/dictionaries", json={"name": "Channel words"}).json()["dictionary_id"]

        class FakeTelegram:
            def __init__(self, _token): pass
            def is_channel_admin(self, channel_id, actor_id): return actor_id in {7001, 999}
            def call(self, method, **kwargs):
                if method == "getMe": return {"id": 999}
                if method == "getChat": return {"id": kwargs["chat_id"], "title": "Study"}
                raise AssertionError(method)

        with patch("chinese_bot.telegram.Telegram", FakeTelegram):
            denied = self.member.post("/api/channels/link", json={"channel_id": -1001, "dictionary_id": dictionary})
            self.assertEqual(denied.status_code, 403)
            linked = self.owner.post("/api/channels/link", json={"channel_id": -1001, "dictionary_id": dictionary})
            self.assertEqual(linked.status_code, 200, linked.text)
            self.assertTrue(Store(str(self.path)).channel_link(-1001)["enabled"])
            unlinked = self.owner.post("/api/channels/unlink", json={"channel_id": -1001, "dictionary_id": dictionary})
            self.assertEqual(unlinked.status_code, 200, unlinked.text)
            self.assertFalse(Store(str(self.path)).channel_link(-1001)["enabled"])


if __name__ == "__main__":
    unittest.main()
