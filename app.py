import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

DB_PATH = os.getenv("BOT_DB_PATH", "/data/vocabulary.sqlite3")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://chinese.qlgn.space")
STATIC = Path(__file__).parent / "web"
app = FastAPI(title="Chinese Pocket", docs_url=None, redoc_url=None)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}

class WordIn(BaseModel):
    hanzi: str
    pinyin: str
    translation: str

class DictionaryIn(BaseModel):
    name: str

class SelectDictionaryIn(BaseModel):
    dictionary_id: int

class InviteIn(BaseModel):
    dictionary_id: int

class RedeemIn(BaseModel):
    code: str

class RemoveMemberIn(BaseModel):
    dictionary_id: int
    telegram_id: int

class ChannelLinkIn(BaseModel):
    channel_id: int
    dictionary_id: int

class ChannelIn(BaseModel):
    channel_id: int

class MembersIn(BaseModel):
    dictionary_id: int



def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS words (
        id INTEGER PRIMARY KEY, hanzi TEXT NOT NULL, pinyin TEXT NOT NULL,
        translation TEXT NOT NULL, UNIQUE(hanzi,pinyin,translation))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    conn.execute("INSERT OR IGNORE INTO settings VALUES ('time','09:00'),('zone','UTC')")
    conn.execute("""CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY, subscribed INTEGER NOT NULL DEFAULT 0, mode TEXT, last_review TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
        created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_words (
        id INTEGER PRIMARY KEY, telegram_id INTEGER NOT NULL, hanzi TEXT NOT NULL,
        pinyin TEXT NOT NULL, translation TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(telegram_id, hanzi, pinyin, translation)
    )""")
    from .store import init_dictionary_schema
    init_dictionary_schema(conn)
    conn.commit()
    return conn


def telegram_user(init_data: str) -> dict[str, Any]:
    if not init_data or not BOT_TOKEN:
        raise HTTPException(401, "Telegram session is required")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    auth_date = int(pairs.get("auth_date", "0"))
    if not received or not auth_date or time.time() - auth_date > 86400:
        raise HTTPException(401, "Telegram session expired")
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(401, "Invalid Telegram signature")
    try:
        user = json.loads(pairs["user"])
        if not isinstance(user.get("id"), int):
            raise ValueError
        return user
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram user") from None


def current_user(session: str | None) -> dict[str, Any]:
    if not session or not APP_SECRET:
        raise HTTPException(401, "Not authenticated")
    try:
        raw, signature = session.rsplit(".", 1)
        if not hmac.compare_digest(signature, hmac.new(APP_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()):
            raise ValueError
        payload = json.loads(raw)
        if payload["exp"] < time.time():
            raise ValueError
        return payload["user"]
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Session expired") from None


def make_session(user: dict[str, Any]) -> str:
    raw = json.dumps({"user": user, "exp": time.time() + 604800}, separators=(",", ":"), ensure_ascii=False)
    sig = hmac.new(APP_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


@app.get("/healthz")
def healthz():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/auth")
def auth(payload: dict[str, str], response: Response):
    user = telegram_user(payload.get("initData", ""))
    now = int(time.time())
    with db() as conn:
        conn.execute("""INSERT INTO users(telegram_id, username, first_name, last_name, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET
            username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name, last_seen=excluded.last_seen""",
            (user["id"], user.get("username", ""), user.get("first_name", ""), user.get("last_name", ""), now, now))
    from .store import Store
    Store(DB_PATH).personal_dictionary(user["id"])
    response.set_cookie("session", make_session(user), httponly=True, secure=True, samesite="lax", max_age=604800)
    return {"user": user}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    store = Store(DB_PATH)
    store.personal_dictionary(user["id"])
    dictionaries = store.dictionaries_for(user["id"])
    selected = store.selected_dictionary(user["id"])
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM dictionary_words WHERE dictionary_id = ?", (selected,)).fetchone()[0]
    return {"user": user, "total": total, "dictionary_id": selected,
            "dictionaries": [dict(row) for row in dictionaries]}


def require_dictionary_member(conn, dictionary_id, user_id, owner=False):
    row = conn.execute("SELECT role FROM dictionary_members WHERE dictionary_id=? AND telegram_id=?",
                       (dictionary_id, user_id)).fetchone()
    if not row or (owner and row["role"] != "owner"):
        raise HTTPException(403, "Нет доступа к словарю")
    return row["role"]


@app.get("/api/dictionaries")
def list_dictionaries(session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    store = Store(DB_PATH)
    store.personal_dictionary(user["id"])
    return {"selected": store.selected_dictionary(user["id"]),
            "dictionaries": [dict(row) for row in store.dictionaries_for(user["id"])]}


@app.post("/api/dictionaries")
def create_dictionary(payload: DictionaryIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    name = payload.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "Название должно быть от 1 до 80 символов")
    from .store import Store
    dictionary_id = Store(DB_PATH).create_dictionary(user["id"], name)
    return {"dictionary_id": dictionary_id, "name": name}


@app.post("/api/dictionaries/select")
def select_dictionary(payload: SelectDictionaryIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    if not Store(DB_PATH).select_dictionary(user["id"], payload.dictionary_id):
        raise HTTPException(403, "Нет доступа к словарю")
    return {"selected": payload.dictionary_id}


@app.post("/api/dictionaries/invite")
def create_invite(payload: InviteIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    if not user.get("username"):
        raise HTTPException(400, "Для приглашений задайте Telegram username в профиле")
    code = secrets.token_urlsafe(9)
    from .store import Store
    if not Store(DB_PATH).create_invite(payload.dictionary_id, user["id"], code, int(time.time()) + 7 * 86400):
        raise HTTPException(403, "Приглашения может создавать только владелец")
    return {"code": code, "expires_at": int(time.time()) + 7 * 86400}


@app.post("/api/dictionaries/redeem")
def redeem_invite(payload: RedeemIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    dictionary_id = Store(DB_PATH).redeem_invite(payload.code.strip(), user["id"])
    if not dictionary_id:
        raise HTTPException(400, "Код недействителен, просрочен или уже использован")
    return {"dictionary_id": dictionary_id}


@app.get("/api/dictionaries/{dictionary_id}/members")
def list_dictionary_members(dictionary_id: int, session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    members = Store(DB_PATH).members(dictionary_id, user["id"])
    if members is None:
        raise HTTPException(403, "Нет доступа к словарю")
    return {"members": [dict(member) for member in members]}


@app.delete("/api/dictionaries/members")
def remove_member(payload: RemoveMemberIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    if not Store(DB_PATH).remove_member(payload.dictionary_id, user["id"], payload.telegram_id):
        raise HTTPException(403, "Удалить участника может только владелец")
    return {"ok": True}


@app.post("/api/channels/link")
def link_channel(payload: ChannelLinkIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        require_dictionary_member(conn, payload.dictionary_id, user["id"], owner=True)
    if not BOT_TOKEN:
        raise HTTPException(503, "Telegram bot is not configured")
    from .telegram import Telegram
    api = Telegram(BOT_TOKEN)
    if not api.is_channel_admin(payload.channel_id, user["id"]):
        raise HTTPException(403, "Подключить канал может только его администратор")
    bot = api.call("getMe")
    if not api.is_channel_admin(payload.channel_id, bot["id"]):
        raise HTTPException(400, "Сначала добавьте бота в канал администратором")
    info = api.call("getChat", chat_id=payload.channel_id)
    from .store import Store
    Store(DB_PATH).link_channel(payload.channel_id, info.get("title", ""), payload.dictionary_id, True)
    return {"channel_id": payload.channel_id, "dictionary_id": payload.dictionary_id, "enabled": True}


@app.post("/api/channels/unlink")
def unlink_channel(payload: ChannelLinkIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        require_dictionary_member(conn, payload.dictionary_id, user["id"], owner=True)
        row = conn.execute("SELECT dictionary_id FROM channel_links WHERE channel_id=?", (payload.channel_id,)).fetchone()
        if not row or row["dictionary_id"] != payload.dictionary_id:
            raise HTTPException(404, "Канал не связан с этим словарём")
    from .store import Store
    Store(DB_PATH).unlink_channel(payload.channel_id)
    return {"ok": True}


@app.get("/api/channels")
def list_channels(session: str | None = Cookie(default=None)):
    user = current_user(session)
    from .store import Store
    return {"channels": [dict(row) for row in Store(DB_PATH).linked_channels_for_owner(user["id"])]}


@app.post("/api/channels/check")
def check_channel(payload: ChannelIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    if not BOT_TOKEN:
        raise HTTPException(503, "Telegram bot is not configured")
    from .telegram import Telegram
    api = Telegram(BOT_TOKEN)
    if not api.is_channel_admin(payload.channel_id, user["id"]):
        raise HTTPException(403, "Пользователь должен быть администратором этого канала")
    bot = api.call("getMe")
    if not api.is_channel_admin(payload.channel_id, bot["id"]):
        raise HTTPException(400, "Сначала добавьте бота администратором канала")
    return api.call("getChat", chat_id=payload.channel_id)


@app.get("/api/words")
def words(session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        dictionary_id = conn.execute("SELECT selected_dictionary_id FROM users WHERE telegram_id=?", (user["id"],)).fetchone()[0]
        require_dictionary_member(conn, dictionary_id, user["id"])
        rows = conn.execute("SELECT id, hanzi, pinyin, translation FROM dictionary_words WHERE dictionary_id=? ORDER BY id DESC LIMIT 100", (dictionary_id,)).fetchall()
    return {"words": [dict(row) for row in rows]}


@app.post("/api/words")
def add_word(word: WordIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    values = tuple(v.strip() for v in (word.hanzi, word.pinyin, word.translation))
    if not all(values) or len(values[0]) > 40 or len(values[1]) > 80 or len(values[2]) > 150:
        raise HTTPException(400, "Заполните все поля")
    with db() as conn:
        dictionary_id = conn.execute("SELECT selected_dictionary_id FROM users WHERE telegram_id=?", (user["id"],)).fetchone()[0]
        require_dictionary_member(conn, dictionary_id, user["id"])
        cur = conn.execute("INSERT OR IGNORE INTO dictionary_words(dictionary_id,hanzi,pinyin,translation) VALUES (?,?,?,?)", (dictionary_id, *values))
        row = conn.execute("SELECT id,hanzi,pinyin,translation FROM dictionary_words WHERE dictionary_id=? AND hanzi=? AND pinyin=? AND translation=?", (dictionary_id, *values)).fetchone()
    return {"word": dict(row), "created": cur.rowcount == 1}


@app.get("/api/review")
def review(session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        dictionary_id = conn.execute("SELECT selected_dictionary_id FROM users WHERE telegram_id=?", (user["id"],)).fetchone()[0]
        require_dictionary_member(conn, dictionary_id, user["id"])
        rows = conn.execute("SELECT id,hanzi,pinyin,translation FROM dictionary_words WHERE dictionary_id=? ORDER BY random() LIMIT 5", (dictionary_id,)).fetchall()
    return {"words": [dict(row) for row in rows]}
