import re
import sqlite3
import json
import os
import tempfile
import fcntl
import time as system_time
from contextlib import contextmanager
from datetime import time as clock_time
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
        clock_time.fromisoformat(parts[0])
        ZoneInfo(parts[1])
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Проверьте время и название часового пояса IANA.") from None
    return tuple(parts)


def init_dictionary_schema(db):
    # Serialize across independent bot/web processes sharing the persistent SQLite volume.
    database_path = db.execute("PRAGMA database_list").fetchone()[2]
    lock_path = database_path + ".dictionary-migration.lock"
    with open(lock_path, "a") as migration_lock:
        fcntl.flock(migration_lock.fileno(), fcntl.LOCK_EX)
        _init_dictionary_schema_locked(db)


def _init_dictionary_schema_locked(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS dictionaries (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('personal','shared')),
            owner_id INTEGER, created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS dictionary_members (
            dictionary_id INTEGER NOT NULL REFERENCES dictionaries(id) ON DELETE CASCADE,
            telegram_id INTEGER NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','member')),
            joined_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY(dictionary_id, telegram_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dictionary_members_user ON dictionary_members(telegram_id);
        CREATE TABLE IF NOT EXISTS dictionary_invites (
            code TEXT PRIMARY KEY, dictionary_id INTEGER NOT NULL REFERENCES dictionaries(id) ON DELETE CASCADE,
            created_by INTEGER NOT NULL, created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            expires_at INTEGER NOT NULL, redeemed_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS dictionary_words (
            id INTEGER PRIMARY KEY, dictionary_id INTEGER NOT NULL REFERENCES dictionaries(id) ON DELETE CASCADE,
            hanzi TEXT NOT NULL, pinyin TEXT NOT NULL, translation TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(dictionary_id, hanzi, pinyin, translation)
        );
        CREATE INDEX IF NOT EXISTS idx_dictionary_words_dictionary ON dictionary_words(dictionary_id);
        CREATE TABLE IF NOT EXISTS imports (
            token TEXT PRIMARY KEY, dictionary_id INTEGER REFERENCES dictionaries(id),
            scope_type TEXT NOT NULL, scope_id INTEGER NOT NULL, words TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_imports_scope ON imports(scope_type, scope_id, status);
        CREATE TABLE IF NOT EXISTS channel_words (
            id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, hanzi TEXT NOT NULL,
            pinyin TEXT NOT NULL, translation TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(channel_id, hanzi, pinyin, translation)
        );
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
            subscribed INTEGER NOT NULL DEFAULT 1, last_review TEXT
        );
        CREATE TABLE IF NOT EXISTS app_migrations (
            migration TEXT PRIMARY KEY, applied_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
    """)
    channel_columns = {row[1] for row in db.execute("PRAGMA table_info(channel_links)")}
    if not channel_columns:
        db.execute("""CREATE TABLE channel_links (
            channel_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            dictionary_id INTEGER REFERENCES dictionaries(id), enabled INTEGER NOT NULL DEFAULT 0,
            last_review TEXT)""")
    elif "dictionary_id" not in channel_columns:
        db.execute("ALTER TABLE channel_links ADD COLUMN dictionary_id INTEGER REFERENCES dictionaries(id)")
        db.execute("UPDATE channel_links SET enabled=0")
    import_columns = {row[1] for row in db.execute("PRAGMA table_info(imports)")}
    if import_columns and "dictionary_id" not in import_columns:
        db.execute("ALTER TABLE imports ADD COLUMN dictionary_id INTEGER REFERENCES dictionaries(id)")
    # Keep user selection durable across Mini App sessions.
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if columns and "selected_dictionary_id" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN selected_dictionary_id INTEGER")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_migrations (
            migration TEXT PRIMARY KEY, applied_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );
    """)
    migration_done = db.execute("SELECT 1 FROM app_migrations WHERE migration='dictionary-v1'").fetchone()
    legacy_cleanup_done = db.execute("SELECT 1 FROM app_migrations WHERE migration='discard-legacy-v2'").fetchone()
    if not legacy_cleanup_done:
        create_pre_migration_backup(db)

    # Old bot-global words and channel vocabularies are intentionally discarded exactly once.
    # Mini App personal words are migrated into the corresponding personal dictionaries below.
    for channel in db.execute("SELECT id,title,last_review FROM channels").fetchall():
        db.execute("INSERT OR IGNORE INTO channel_links(channel_id,title,dictionary_id,enabled,last_review) VALUES (?,?,NULL,0,?)",
                   (channel[0], channel[1], channel[2]))
    if not migration_done:
        db.execute("UPDATE channel_links SET enabled=0 WHERE dictionary_id IS NULL")
        db.execute("DELETE FROM words")
        db.execute("DELETE FROM channel_words")
        db.execute("DELETE FROM imports WHERE dictionary_id IS NULL")
    # Migrate old Mini App words into each owner's personal dictionary once.
    if columns:
        for user in db.execute("SELECT telegram_id FROM users").fetchall():
            user_id = user[0]
            row = db.execute("SELECT id FROM dictionaries WHERE kind='personal' AND owner_id=?", (user_id,)).fetchone()
            if row is None:
                db.execute("INSERT INTO dictionaries(name,kind,owner_id) VALUES ('Личный словарь','personal',?)", (user_id,))
                dictionary_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                db.execute("INSERT OR IGNORE INTO dictionary_members(dictionary_id,telegram_id,role) VALUES (?,?,'owner')",
                           (dictionary_id, user_id))
            else:
                dictionary_id = row[0]
            db.execute("""INSERT OR IGNORE INTO dictionary_words(dictionary_id,hanzi,pinyin,translation,created_at)
                SELECT ?,hanzi,pinyin,translation,created_at FROM user_words WHERE telegram_id=?""",
                       (dictionary_id, user_id))
            if "selected_dictionary_id" in columns:
                db.execute("UPDATE users SET selected_dictionary_id=COALESCE(selected_dictionary_id,?) WHERE telegram_id=?",
                           (dictionary_id, user_id))
    if not migration_done:
        db.execute("INSERT INTO app_migrations(migration) VALUES ('dictionary-v1')")
    if not legacy_cleanup_done:
        db.execute("UPDATE channel_links SET enabled=0,dictionary_id=NULL WHERE dictionary_id IN (SELECT id FROM dictionaries WHERE kind='legacy')")
        db.execute("DELETE FROM imports WHERE dictionary_id IN (SELECT id FROM dictionaries WHERE kind='legacy') OR dictionary_id IS NULL")
        db.execute("DELETE FROM dictionary_words WHERE dictionary_id IN (SELECT id FROM dictionaries WHERE kind='legacy')")
        db.execute("DELETE FROM dictionaries WHERE kind='legacy'")
        db.execute("INSERT INTO app_migrations(migration) VALUES ('discard-legacy-v2')")


def create_pre_migration_backup(db):
    database_path = db.execute("PRAGMA database_list").fetchone()[2]
    if not database_path:
        return None
    backup_path = Path(database_path + ".pre-dictionary-v1.sqlite3")
    if backup_path.exists():
        os.chmod(backup_path, 0o600)
        with sqlite3.connect(backup_path) as backup:
            result = backup.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Existing pre-migration backup is not valid")
        return backup_path
    fd, temporary = tempfile.mkstemp(prefix=backup_path.name + ".", suffix=".tmp", dir=backup_path.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as target:
            db.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("Could not verify pre-migration backup")
        os.chmod(temporary, 0o600)
        os.replace(temporary, backup_path)
        fd = os.open(backup_path, os.O_RDONLY)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        return backup_path
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
        db.execute("UPDATE channel_links SET enabled=0 WHERE dictionary_id IS NULL")


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "words" not in existing:
                db.executescript("CREATE TABLE words (id INTEGER PRIMARY KEY, hanzi TEXT NOT NULL, pinyin TEXT NOT NULL, translation TEXT NOT NULL, UNIQUE(hanzi,pinyin,translation));")
            if "users" not in existing:
                db.executescript("CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);")
            if "user_words" not in existing:
                db.executescript("CREATE TABLE user_words (id INTEGER PRIMARY KEY, telegram_id INTEGER NOT NULL, hanzi TEXT NOT NULL, pinyin TEXT NOT NULL, translation TEXT NOT NULL, created_at INTEGER NOT NULL, UNIQUE(telegram_id,hanzi,pinyin,translation));")
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
            init_dictionary_schema(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, words, dictionary_id=None):
        if dictionary_id is not None:
            return self.add_dictionary_words(dictionary_id, words)
        with self.connect() as db:
            return 0

    def personal_dictionary(self, telegram_id, name="Личный словарь"):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO users(telegram_id,username,first_name,last_name,created_at,last_seen) VALUES (?, '', '', '', ?, ?)",
                       (telegram_id, int(system_time.time()), int(system_time.time())))
            row = db.execute("SELECT id FROM dictionaries WHERE kind='personal' AND owner_id=?", (telegram_id,)).fetchone()
            if row:
                return row["id"]
            db.execute("INSERT INTO dictionaries(name,kind,owner_id) VALUES (?,'personal',?)", (name, telegram_id))
            dictionary_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT OR IGNORE INTO dictionary_members(dictionary_id,telegram_id,role) VALUES (?,?,'owner')",
                       (dictionary_id, telegram_id))
            db.execute("UPDATE users SET selected_dictionary_id=? WHERE telegram_id=?", (dictionary_id, telegram_id))
            return dictionary_id

    def create_dictionary(self, owner_id, name):
        with self.connect() as db:
            db.execute("INSERT INTO dictionaries(name,kind,owner_id) VALUES (?,'shared',?)", (name, owner_id))
            dictionary_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO dictionary_members(dictionary_id,telegram_id,role) VALUES (?,?,'owner')",
                       (dictionary_id, owner_id))
            return dictionary_id

    def dictionaries_for(self, telegram_id):
        with self.connect() as db:
            return db.execute("SELECT d.id,d.name,d.kind,m.role FROM dictionaries d JOIN dictionary_members m ON m.dictionary_id=d.id WHERE m.telegram_id=? ORDER BY d.id", (telegram_id,)).fetchall()

    def dictionary_role(self, dictionary_id, telegram_id):
        with self.connect() as db:
            row = db.execute("SELECT role FROM dictionary_members WHERE dictionary_id=? AND telegram_id=?", (dictionary_id, telegram_id)).fetchone()
            return row["role"] if row else None

    def select_dictionary(self, telegram_id, dictionary_id):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM dictionary_members WHERE dictionary_id=? AND telegram_id=?", (dictionary_id, telegram_id)).fetchone():
                return False
            db.execute("UPDATE users SET selected_dictionary_id=? WHERE telegram_id=?", (dictionary_id, telegram_id))
            return True

    def channel_due(self, day):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT channel_id FROM channel_links WHERE enabled=1 AND (last_review IS NULL OR last_review<>?)", (day,))]

    def mark_channel_review(self, channel_id, day):
        with self.connect() as db:
            db.execute("UPDATE channel_links SET last_review=? WHERE channel_id=?", (day, channel_id))

    def create_invite(self, dictionary_id, owner_id, code, expires_at):
        with self.connect() as db:
            if self.dictionary_role(dictionary_id, owner_id) != "owner":
                return False
            db.execute("INSERT INTO dictionary_invites(code,dictionary_id,created_by,expires_at) VALUES (?,?,?,?)", (code, dictionary_id, owner_id, expires_at))
            return True

    def redeem_invite(self, code, user_id):
        with self.connect() as db:
            invite = db.execute("SELECT * FROM dictionary_invites WHERE code=?", (code,)).fetchone()
            if not invite or invite["redeemed_by"] is not None or invite["expires_at"] < system_time.time():
                return None
            db.execute("INSERT OR IGNORE INTO dictionary_members(dictionary_id,telegram_id,role) VALUES (?,?,'member')", (invite["dictionary_id"], user_id))
            db.execute("UPDATE dictionary_invites SET redeemed_by=? WHERE code=? AND redeemed_by IS NULL", (user_id, code))
            return invite["dictionary_id"]

    def members(self, dictionary_id, requester_id):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM dictionary_members WHERE dictionary_id=? AND telegram_id=?", (dictionary_id, requester_id)).fetchone():
                return None
            return db.execute("""SELECT u.telegram_id,u.username,u.first_name,u.last_name,m.role
                FROM dictionary_members m JOIN users u ON u.telegram_id=m.telegram_id
                WHERE m.dictionary_id=? ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END,u.telegram_id""",
                              (dictionary_id,)).fetchall()

    def remove_member(self, dictionary_id, owner_id, user_id):
        with self.connect() as db:
            if self.dictionary_role(dictionary_id, owner_id) != "owner" or owner_id == user_id:
                return False
            db.execute("DELETE FROM dictionary_members WHERE dictionary_id=? AND telegram_id=? AND role='member'", (dictionary_id, user_id))
            return db.total_changes > 0

    def list_dictionary_words(self, dictionary_id, page=1):
        with self.connect() as db:
            return db.execute("SELECT id,hanzi,pinyin,translation FROM dictionary_words WHERE dictionary_id=? ORDER BY id DESC LIMIT 100 OFFSET ?", (dictionary_id, (page-1)*100)).fetchall()

    def add_dictionary_words(self, dictionary_id, words):
        with self.connect() as db:
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO dictionary_words(dictionary_id,hanzi,pinyin,translation) VALUES (?,?,?,?)", [(dictionary_id,*word) for word in words])
            return db.total_changes - before

    def selected_dictionary(self, telegram_id):
        with self.connect() as db:
            row = db.execute("SELECT selected_dictionary_id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            return row["selected_dictionary_id"] if row else None

    def link_channel(self, channel_id, title, dictionary_id, enabled=True):
        with self.connect() as db:
            db.execute("INSERT INTO channel_links(channel_id,title,dictionary_id,enabled) VALUES (?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,dictionary_id=excluded.dictionary_id,enabled=excluded.enabled", (channel_id,title,dictionary_id,int(enabled)))

    def channel_link(self, channel_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM channel_links WHERE channel_id=?", (channel_id,)).fetchone()
            return dict(row) if row else None

    def unlink_channel(self, channel_id):
        with self.connect() as db:
            db.execute("UPDATE channel_links SET enabled=0 WHERE channel_id=?", (channel_id,))

    def linked_channels_for_owner(self, telegram_id):
        with self.connect() as db:
            return db.execute("""SELECT c.channel_id,c.title,c.dictionary_id,d.name AS dictionary_name,c.enabled
                FROM channel_links c JOIN dictionaries d ON d.id=c.dictionary_id
                JOIN dictionary_members m ON m.dictionary_id=d.id
                WHERE m.telegram_id=? AND m.role='owner' AND c.dictionary_id IS NOT NULL
                ORDER BY c.channel_id""", (telegram_id,)).fetchall()

    def dictionary_words(self, dictionary_id, *, review=False):
        with self.connect() as db:
            query = "SELECT hanzi,pinyin,translation FROM dictionary_words WHERE dictionary_id=?"
            query += " ORDER BY random() LIMIT 5" if review else " ORDER BY id"
            return db.execute(query, (dictionary_id,)).fetchall()

    def words(self, page=1, *, review=False):
        return []

    def schedule(self):
        with self.connect() as db:
            settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}
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

    def channel(self, channel_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM channel_links WHERE channel_id = ?", (channel_id,)).fetchone()
            return dict(row) if row else None

    def set_channel(self, channel_id, title, enabled):
        with self.connect() as db:
            existing = db.execute("SELECT dictionary_id FROM channel_links WHERE channel_id=?", (channel_id,)).fetchone()
            if enabled:
                if not existing or not existing[0]:
                    return False
                db.execute("UPDATE channel_links SET title=?,enabled=1 WHERE channel_id=?", (title, channel_id))
            else:
                db.execute("""INSERT INTO channel_links(channel_id,title,dictionary_id,enabled) VALUES (?,?,NULL,0)
                    ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,enabled=0""", (channel_id,title))
            return True

    def channel_words(self, channel_id, *, review=False):
        with self.connect() as db:
            link = db.execute("SELECT dictionary_id FROM channel_links WHERE channel_id=? AND enabled=1", (channel_id,)).fetchone()
            if not link:
                return []
            query = "SELECT hanzi,pinyin,translation FROM dictionary_words WHERE dictionary_id=?"
            query += " ORDER BY random() LIMIT 5" if review else " ORDER BY id"
            return db.execute(query, (link[0],)).fetchall()

    def add_channel_words(self, channel_id, words):
        link = self.channel_link(channel_id)
        return self.add_dictionary_words(link["dictionary_id"], words) if link and link["enabled"] and link["dictionary_id"] else 0

    def create_import(self, scope_type, scope_id, words, token, dictionary_id=None):
        if scope_type == "channel" and dictionary_id is None:
            link = self.channel_link(scope_id)
            dictionary_id = link["dictionary_id"] if link and link["enabled"] else None
        elif scope_type == "user" and dictionary_id is None:
            dictionary_id = self.personal_dictionary(scope_id)
        if dictionary_id is None:
            raise ValueError("Не выбран словарь для импорта")
        with self.connect() as db:
            db.execute("INSERT INTO imports(token, dictionary_id, scope_type, scope_id, words) VALUES (?, ?, ?, ?, ?)",
                       (token, dictionary_id, scope_type, scope_id, json.dumps(words, ensure_ascii=False)))

    def get_import(self, token):
        with self.connect() as db:
            row = db.execute("SELECT * FROM imports WHERE token = ?", (token,)).fetchone()
            return dict(row) if row else None

    def confirm_import(self, token, scope_type, scope_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM imports WHERE token = ? AND scope_type = ? AND scope_id = ?",
                             (token, scope_type, scope_id)).fetchone()
            if not row or row['status'] != 'pending' or int(row['created_at']) < system_time.time() - 900:
                if row and row['status'] == 'pending':
                    db.execute("UPDATE imports SET status='rejected' WHERE token=?", (token,))
                return 0
            if scope_type == 'channel':
                link = db.execute("SELECT dictionary_id,enabled FROM channel_links WHERE channel_id=?", (scope_id,)).fetchone()
                if not link or not link['enabled'] or link['dictionary_id'] != row['dictionary_id']:
                    return 0
            elif not db.execute("SELECT 1 FROM dictionary_members WHERE dictionary_id=? AND telegram_id=?",
                                (row['dictionary_id'], scope_id)).fetchone():
                return 0
            words = json.loads(row['words'])
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO dictionary_words(dictionary_id,hanzi,pinyin,translation) VALUES (?,?,?,?)",
                           [(row['dictionary_id'], *word) for word in words])
            db.execute("UPDATE imports SET status = 'confirmed' WHERE token = ?", (token,))
            return db.total_changes - before - 1

    def pending_imports_expire(self):
        with self.connect() as db:
            db.execute("UPDATE imports SET status='rejected' WHERE status='pending' AND created_at < ?", (int(system_time.time()) - 900,))

    def import_status(self, token):
        row = self.get_import(token)
        return row['status'] if row else None

    def reject_import(self, token, scope_type, scope_id):
        with self.connect() as db:
            db.execute("UPDATE imports SET status='rejected' WHERE token=? AND scope_type=? AND scope_id=? AND status='pending'",
                       (token, scope_type, scope_id))
