import logging
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime, time
from time import monotonic, sleep
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .store import HANZI, WORD_FORMAT, Store, parse_schedule, parse_words
from .telegram import Telegram, TelegramError
from .speech import transcribe

DEFAULT_ENV_FILE = "/home/ilya/Projects/telegram-chinese-bot/.env"
TAG = "#слово"
COMMANDS = {
    "start": "Меню",
    "help": "Справка",
    "add": "Добавить слова",
    "list": "Словарь",
    "schedule": "Расписание",
    "subscribe": "Включить рассылку",
    "unsubscribe": "Выключить рассылку",
    "cancel": "Отменить ввод",
    "confirm": "Подтвердить импорт",
    "channel_on": "Включить канал",
    "channel_off": "Выключить канал",
}
MENU = {
    "keyboard": [
        [{"text": COMMANDS["add"]}, {"text": COMMANDS["list"]}],
        [{"text": COMMANDS["schedule"]}],
        [{"text": COMMANDS["subscribe"]}, {"text": COMMANDS["unsubscribe"]}],
    ],
    "resize_keyboard": True,
}
logger = logging.getLogger(__name__)


def load_token():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        path = os.getenv("BOT_ENV_FILE", DEFAULT_ENV_FILE)
        values = dotenv_values(path, interpolate=False, verbose=False, encoding="utf-8-sig")
        token = values.get("TELEGRAM_BOT_TOKEN") or values.get("TELEGRAM_TOKEN")
    if not token or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Задайте корректный TELEGRAM_BOT_TOKEN.")
    return token


def recognize(photo):
    return subprocess.run(
        [os.getenv("BOT_OCR_BIN", "tesseract"), "stdin", "stdout",
         "-l", os.getenv("BOT_OCR_LANG", "chi_sim+eng+rus"), "--psm", "6"],
        input=photo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=20, check=True,
    ).stdout.decode("utf-8").strip()


def format_words(words):
    return "\n".join(" | ".join(word) for word in words)


def utf16_prefix(text, units):
    return text.encode("utf-16-le")[:units * 2].decode("utf-16-le", errors="ignore")


class Bot:
    def __init__(self, api, store, allowed_users=(), *, ocr=recognize, stt=transcribe):
        self.api = api
        self.store = store
        self.allowed_users = allowed_users
        self.ocr = ocr
        self.stt = stt
        self.review_retry = {}

    def send(self, chat, text, **kwargs):
        self.api.call("sendMessage", chat_id=chat, text=text, **kwargs)

    def photo(self, chat, message, *, scope_type="user", scope_id=None):
        scope_id = scope_id or chat
        caption = message.get("caption", "").strip()
        if not re.match(r"^#слово(?=\s|\||$)", caption, re.IGNORECASE):
            self.send(chat, f"Добавьте к фото подпись {TAG}.")
            return
        tail = caption[len(TAG):].strip()
        if tail and not tail.startswith("|"):
            self.send(chat, f"Подпись: {TAG} или {TAG} | nǐ hǎo | привет")
            return
        try:
            raw = self.ocr(self.api.photo(message["photo"][-1]["file_id"]))
        except (TelegramError, OSError, subprocess.SubprocessError, UnicodeError):
            self.send(chat, "Не удалось распознать фото. Попробуйте снова или добавьте слова через /add.")
            return
        if not raw:
            self.send(chat, "Текст на фото не найден. Попробуйте более чёткое фото или /add.")
            return

        # OCR can insert spaces between adjacent Hanzi or recognize a full-width separator.
        lines = []
        for line in raw.replace("｜", "|").splitlines():
            first, separator, rest = line.partition("|")
            first = "".join(first.split())
            lines.append(first + separator + rest if HANZI.fullmatch(first) else line)
        if tail and len(lines) == 1 and HANZI.fullmatch(lines[0]):
            lines[0] += " " + tail

        try:
            words = parse_words("\n".join(lines))
        except ValueError:
            self.send(chat, "Не удалось найти строки в формате: " + WORD_FORMAT)
            return
        token = secrets.token_urlsafe(8)
        dictionary_id = self.store.personal_dictionary(scope_id) if scope_type == "user" else None
        self.store.create_import(scope_type, scope_id, words, token, dictionary_id=dictionary_id)
        preview = utf16_prefix(format_words(words), 3400)
        self.send(chat, f"Распознано:\n{preview}\n\n"
                        f"Подтвердите импорт: /confirm {token}\n"
                        f"Или отмените: /cancel {token}")

    def voice(self, chat, message):
        try:
            text = self.stt(self.api.voice(message["voice"]["file_id"]))
        except (TelegramError, OSError, RuntimeError, ValueError):
            self.send(chat, "Не удалось расшифровать голосовое сообщение. Попробуйте ещё раз.")
            return
        if not text:
            self.send(chat, "В голосовом сообщении не удалось разобрать речь.")
            return
        self.send(chat, f"Транскрипция:\n{text}")

    def handle(self, message):
        chat_data = message["chat"]
        if chat_data["type"] != "private":
            if message.get("photo"):
                channel = self.store.channel(chat_data["id"])
                if channel and channel["enabled"]:
                    self.photo(chat_data["id"], message, scope_type="channel", scope_id=chat_data["id"])
            elif message.get("text", "").strip() in {"/channel_on", "/channel_off"}:
                self.send(chat_data["id"], "Настройку канала нужно выполнить в личном чате: /channel_on -1001234567890")
            return
        chat = chat_data["id"]
        if self.allowed_users and message.get("from", {}).get("id") not in self.allowed_users:
            self.send(chat, "Доступ к этому боту ограничен.")
            return
        if message.get("photo"):
            self.photo(chat, message)
            return
        if message.get("voice"):
            self.voice(chat, message)
            return
        text = message.get("text", "").strip()
        if not text:
            return
        head, *rest = text.split(maxsplit=1)
        arg = rest[0] if rest else ""
        buttons = {label: key for key, label in COMMANDS.items()}
        command = head[1:].split("@", 1)[0] if head.startswith("/") else buttons.get(text)
        mode = self.store.mode(chat)
        if command == "start" or command == "help":
            self.store.set_mode(chat, None)
            self.send(chat, f"Фото списка слов: отправьте с подписью {TAG}. "
                             "Проверьте распознанные строки и подтвердите импорт командой /confirm <код>. "
                             "Для канала: добавьте бота администратором, в Mini App подключите канал к словарю и отправляйте фото с подписью #слово. Подтверждение импорта выполните в личном чате: /confirm <код>.",
                      reply_markup=MENU)
        elif command == "confirm":
            token = arg.strip()
            pending = self.store.get_import(token)
            count = 0
            if pending and pending["scope_type"] == "user" and pending["scope_id"] == chat:
                count = self.store.confirm_import(token, "user", chat)
            elif pending and pending["scope_type"] == "channel" and self.api.is_channel_admin(pending["scope_id"], chat):
                count = self.store.confirm_import(token, "channel", pending["scope_id"])
            self.store.set_mode(chat, None)
            self.send(chat, f"Импортировано слов: {count}." if count else "Импорт не найден, устарел или уже подтверждён.")
        elif command == "cancel":
            token = arg.strip()
            if token:
                pending = self.store.get_import(token)
                if pending and pending["scope_type"] == "user" and pending["scope_id"] == chat:
                    self.store.reject_import(token, "user", chat)
                elif pending and pending["scope_type"] == "channel" and self.api.is_channel_admin(pending["scope_id"], chat):
                    self.store.reject_import(token, "channel", pending["scope_id"])
            self.store.set_mode(chat, None)
            self.send(chat, "Ввод отменён.")
        elif command in ("channel_on", "channel_off"):
            try:
                parts = arg.split(maxsplit=1)
                channel_id = int(parts[0])
            except (ValueError, IndexError):
                self.send(chat, "Укажите ID канала и словаря: /channel_on -1001234567890 42")
                return
            try:
                dictionary_id = int(parts[1]) if len(parts) > 1 else None
            except ValueError:
                dictionary_id = None
            if not self.api.is_channel_admin(channel_id, chat):
                self.send(chat, "Настройку канала может изменить только его администратор.")
                return
            if command == "channel_off":
                self.store.unlink_channel(channel_id)
                self.send(chat, "Канал отключён.")
                return
            if dictionary_id is None or self.store.dictionary_role(dictionary_id, chat) != "owner":
                self.send(chat, "Укажите ID словаря, которым вы владеете: /channel_on -1001234567890 42")
                return
            bot = self.api.call("getMe")
            if not self.api.is_channel_admin(channel_id, bot["id"]):
                self.send(chat, "Добавьте бота администратором канала и повторите команду.")
                return
            info = self.api.call("getChat", chat_id=channel_id)
            self.store.link_channel(channel_id, info.get("title", ""), dictionary_id, True)
            self.send(chat, "Канал подключён к словарю.")
        elif command == "list":
            if arg and not re.fullmatch(r"[1-9][0-9]{0,8}", arg):
                self.send(chat, "Укажите номер страницы, например /list 2.")
                return
            dictionary_id = self.store.selected_dictionary(chat)
            if not dictionary_id:
                dictionary_id = self.store.personal_dictionary(chat)
            words = self.store.list_dictionary_words(dictionary_id, int(arg or "1"))
            if not words:
                self.send(chat, "В выбранном словаре пока нет слов.")
                return
            self.send(chat, format_words([tuple(row)[1:] for row in words]))
        elif command in ("subscribe", "unsubscribe"):
            enabled = command == "subscribe"
            self.store.subscribe(chat, enabled)
            self.send(chat, "Рассылка включена для этого чата." if enabled
                      else "Рассылка выключена для этого чата.")
        elif command == "add" or (command is None and mode == "add"):
            content = arg if command else text
            self.store.set_mode(chat, "add")
            if not content:
                self.send(chat, f"Пришлите до 10 строк, по одному слову на строку:\n{WORD_FORMAT}")
                return
            try:
                count = self.store.add(parse_words(content), self.store.personal_dictionary(chat))
            except ValueError as error:
                self.send(chat, str(error))
                return
            self.store.set_mode(chat, None)
            self.send(chat, f"Добавлено слов: {count}. Точные дубликаты пропущены.")
        elif command == "schedule" or (command is None and mode == "schedule"):
            content = arg if command else text
            self.store.set_mode(chat, "schedule")
            if not content:
                clock, zone = self.store.schedule()
                self.send(chat, f"Общее расписание: {clock} {zone}.\n"
                          "Пришлите новое время и часовой пояс, например 09:00 Europe/Moscow.")
                return
            try:
                schedule = parse_schedule(content)
            except ValueError as error:
                self.send(chat, str(error))
                return
            self.store.set_schedule(schedule)
            self.store.set_mode(chat, None)
            self.send(chat, f"Общее расписание сохранено: {' '.join(schedule)}.")
        else:
            self.send(chat, "Откройте меню командой /start.")

    def review(self, now):
        clock, zone = self.store.schedule()
        local = now.astimezone(ZoneInfo(zone))
        if local.time() < time.fromisoformat(clock):
            return
        day = local.date().isoformat()
        chats = [chat for chat in self.store.due_chats(day)
                 if not self.allowed_users or chat in self.allowed_users]
        for chat in chats:
            dictionary_id = self.store.selected_dictionary(chat) or self.store.personal_dictionary(chat)
            words = self.store.dictionary_words(dictionary_id, review=True)
            if not words:
                continue
            try:
                self.send(chat, "Повторение на сегодня\n\n" + format_words(words))
            except TelegramError as error:
                if error.code == 403:
                    self.store.subscribe(chat, False)
                else:
                    logger.warning("Рассылка: %s", error)
                if error.code == 429:
                    raise
            else:
                self.store.mark_review(chat, day)

        local_day = local.date().isoformat()
        for channel_id in self.store.channel_due(local_day):
            link = self.store.channel_link(channel_id)
            words = self.store.dictionary_words(link["dictionary_id"], review=True) if link else []
            if not words:
                continue
            try:
                self.send(channel_id, "Повторение на сегодня\n\n" + format_words(words))
            except TelegramError as error:
                logger.warning("Канал %s: %s", channel_id, error)
            else:
                self.store.mark_channel_review(channel_id, local_day)

    def review_channel(self, channel_id):
        link = self.store.channel_link(channel_id)
        words = self.store.dictionary_words(link["dictionary_id"], review=True) if link else []
        if words:
            self.send(channel_id, "Повторение на сегодня\n\n" + format_words(words))
            self.store.mark_channel_review(channel_id, datetime.now(UTC).astimezone(
                ZoneInfo(self.store.schedule()[1])).date().isoformat())

    def channel_admin(self, channel_id, user_id):
        return any(admin.get("user", {}).get("id") == user_id
                   for admin in self.api.channel_admins(channel_id))

    def process_update(self, update):
        if "my_chat_member" in update:
            member = update["my_chat_member"]
            chat = member["chat"]
            status = member.get("new_chat_member", {}).get("status")
            if chat.get("type") == "channel":
                old = self.store.channel(chat["id"])
                enabled = bool(old and old["enabled"] and status == "administrator")
                self.store.set_channel(chat["id"], chat.get("title", ""), enabled)
        elif "channel_post" in update:
            post = update["channel_post"]
            channel = self.store.channel(post["chat"]["id"])
            text = post.get("text", "").strip()
            if channel and channel["enabled"] and channel["dictionary_id"] and post.get("photo"):
                caption = post.get("caption", "")
                if re.match(r"^#слово(?=\s|\||$)", caption, re.IGNORECASE):
                    self.photo(post["chat"]["id"], post, scope_type="channel",
                               scope_id=post["chat"]["id"])
            elif channel and channel["enabled"] and text.startswith("/channel_off"):
                actor_id = post.get("sender_chat", {}).get("id")
                if actor_id and self.channel_admin(post["chat"]["id"], actor_id):
                    self.store.unlink_channel(post["chat"]["id"])
            elif channel and channel["enabled"] and channel["dictionary_id"] and text == "/review":
                self.review_channel(post["chat"]["id"])
        elif "message" in update:
            self.handle(update["message"])

    def run(self):
        offset = int(self.store.setting("offset", "0"))
        registered = False
        while True:
            try:
                if not registered:
                    self.api.call("setMyCommands", commands=[
                        {"command": key, "description": label}
                        for key, label in COMMANDS.items() if key not in {"channel_on", "channel_off"}
                    ])
                    self.api.call("setMyCommands", scope={"type": "all_private_chats"}, commands=[
                        {"command": key, "description": label} for key, label in COMMANDS.items()
                    ])
                    self.api.call("setMyCommands", scope={"type": "all_group_chats"}, commands=[])
                    registered = True
                self.store.pending_imports_expire()
                self.review(datetime.now(UTC))
                updates = self.api.call("getUpdates", offset=offset, timeout=10,
                                        allowed_updates=["message", "channel_post", "my_chat_member"])
                for update in updates:
                    try:
                        self.process_update(update)
                    except TelegramError as error:
                        if error.code not in (400, 403):
                            raise
                    offset = update["update_id"] + 1
                    self.store.save_setting("offset", str(offset))
                    self.review(datetime.now(UTC))
            except TelegramError as error:
                logger.warning("%s", error)
                if error.code in (401, 404, 409):
                    raise
                sleep(max(3, error.retry_after))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        token = load_token()
        allowed = {int(value) for value in os.getenv("BOT_ALLOWED_USER_IDS", "").split(",")
                   if value.strip()}
        path = os.getenv("BOT_DB_PATH", "data/vocabulary.sqlite3")
        Bot(Telegram(token), Store(path), allowed).run()
    except KeyboardInterrupt:
        return 0
    except TelegramError as error:
        logger.error("Бот остановлен. %s", error)
    except (ValueError, OSError):
        logger.error("Проверьте настройки токена, доступа и путь к базе.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
