import re
import sqlite3
from contextlib import contextmanager
from datetime import time
from pathlib import Path
from unicodedata import normalize
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORD_FORMAT = "你好 | nǐ hǎo | привет"
HANZI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]+")


def parse_words(text):
    lines = [line for line in text.splitlines() if line.strip()]
    if not 1 <= len(lines) <= 10:
        raise ValueError("Отправьте от 1 до 10 строк.")
    words = []
    for number, line in enumerate(lines, 1):
        parts = [normalize("NFC", part.strip()) for part in line.split("|")]
        if (len(parts) != 3 or not all(parts)
                or not HANZI.fullmatch(parts[0]) or len(parts[0]) > 40
                or len(parts[1]) > 80 or len(parts[2]) > 150):
            raise ValueError(f"Строка {number}: ожидается формат {WORD_FORMAT}")
        words.append(tuple(parts))
    return words


def parse_schedule(text):
    parts = text.split()
    if len(parts) != 2 or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", parts[0]):
        raise ValueError("Формат: ЧЧ:ММ Часовой_пояс. Пример: 09:00 Europe/Moscow")
    try:
        time.fromisoformat(parts[0])
        ZoneInfo(parts[1])
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Проверьте время и название часового пояса IANA.") from None
    return tuple(parts)


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS words (
                    id INTEGER PRIMARY KEY, hanzi TEXT NOT NULL, pinyin TEXT NOT NULL,
                    translation TEXT NOT NULL, UNIQUE(hanzi, pinyin, translation)
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES ('time', '09:00'), ('zone', 'UTC');
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY, subscribed INTEGER NOT NULL DEFAULT 0,
                    mode TEXT, last_review TEXT
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, words):
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO words(hanzi, pinyin, translation) VALUES (?, ?, ?)", words
            )
            return db.total_changes

    def words(self, page=1, *, review=False):
        with self.connect() as db:
            if review:
                return db.execute(
                    "SELECT hanzi, pinyin, translation FROM words ORDER BY random() LIMIT 5"
                ).fetchall()
            return db.execute("""
                SELECT hanzi, pinyin, translation FROM words ORDER BY id LIMIT 10 OFFSET ?
                """, ((page - 1) * 10,)).fetchall()

    def schedule(self):
        with self.connect() as db:
            settings = dict(db.execute("SELECT key, value FROM settings"))
            return settings["time"], settings["zone"]

    def set_schedule(self, schedule):
        with self.connect() as db:
            db.executemany("UPDATE settings SET value = ? WHERE key = ?",
                           zip(schedule, ("time", "zone")))

    def setting(self, key, default):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def save_setting(self, key, value):
        with self.connect() as db:
            db.execute("REPLACE INTO settings VALUES (?, ?)", (key, value))

    def mode(self, chat):
        with self.connect() as db:
            row = db.execute("SELECT mode FROM chats WHERE id = ?", (chat,)).fetchone()
            return row[0] if row else None

    def set_mode(self, chat, mode):
        with self.connect() as db:
            db.execute("""
                INSERT INTO chats(id, mode) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET mode = excluded.mode
                """, (chat, mode))

    def subscribe(self, chat, enabled):
        with self.connect() as db:
            db.execute("""
                INSERT INTO chats(id, subscribed) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET subscribed = excluded.subscribed
                """, (chat, enabled))

    def due_chats(self, day):
        with self.connect() as db:
            return [row[0] for row in db.execute("""
                SELECT id FROM chats WHERE subscribed = 1
                AND (last_review IS NULL OR last_review <> ?) ORDER BY id
                """, (day,))]

    def mark_review(self, chat, day):
        with self.connect() as db:
            db.execute("UPDATE chats SET last_review = ? WHERE id = ?", (day, chat))
