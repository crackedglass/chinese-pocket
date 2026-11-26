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

class WordIn(BaseModel):
    hanzi: str
    pinyin: str
    translation: str


def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
        created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_words (
        id INTEGER PRIMARY KEY, telegram_id INTEGER NOT NULL, hanzi TEXT NOT NULL,
        pinyin TEXT NOT NULL, translation TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(telegram_id, hanzi, pinyin, translation)
    )""")
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
    response.set_cookie("session", make_session(user), httponly=True, secure=True, samesite="lax", max_age=604800)
    return {"user": user}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM user_words WHERE telegram_id = ?", (user["id"],)).fetchone()[0]
    return {"user": user, "total": total}


@app.get("/api/words")
def words(session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        rows = conn.execute("SELECT id, hanzi, pinyin, translation FROM user_words WHERE telegram_id=? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
    return {"words": [dict(row) for row in rows]}


@app.post("/api/words")
def add_word(word: WordIn, session: str | None = Cookie(default=None)):
    user = current_user(session)
    values = tuple(v.strip() for v in (word.hanzi, word.pinyin, word.translation))
    if not all(values) or len(values[0]) > 40 or len(values[1]) > 80 or len(values[2]) > 150:
        raise HTTPException(400, "Заполните все поля")
    with db() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO user_words(telegram_id, hanzi, pinyin, translation, created_at) VALUES (?, ?, ?, ?, ?)", (user["id"], *values, int(time.time())))
        row = conn.execute("SELECT id, hanzi, pinyin, translation FROM user_words WHERE telegram_id=? AND hanzi=? AND pinyin=? AND translation=?", (user["id"], *values)).fetchone()
    return {"word": dict(row), "created": cur.rowcount == 1}


@app.get("/api/review")
def review(session: str | None = Cookie(default=None)):
    user = current_user(session)
    with db() as conn:
        rows = conn.execute("SELECT id, hanzi, pinyin, translation FROM user_words WHERE telegram_id=? ORDER BY random() LIMIT 5", (user["id"],)).fetchall()
    return {"words": [dict(row) for row in rows]}
