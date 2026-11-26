import logging
import os
import re
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
    "add": "Добавить слова",
    "list": "Словарь",
    "schedule": "Расписание",
    "subscribe": "Включить рассылку",
    "unsubscribe": "Выключить рассылку",
    "cancel": "Отменить ввод",
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

    def photo(self, chat, message):
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

        preview = utf16_prefix("\n".join(lines), 3400)
        self.store.set_mode(chat, "add")
        self.send(chat, f"Распознано:\n{preview}\n\n"
                        f"Проверьте текст и пришлите нужные строки в формате:\n{WORD_FORMAT}\n"
                        "Для отмены отправьте /cancel.")

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
        if command == "start":
            self.store.set_mode(chat, None)
            self.send(chat, f"Общий словарь китайского. Для импорта пришлите фото с подписью {TAG}.",
                      reply_markup=MENU)
        elif command == "cancel":
            self.store.set_mode(chat, None)
            self.send(chat, "Ввод отменён.")
        elif command == "list":
            if arg and not re.fullmatch(r"[1-9][0-9]{0,8}", arg):
                self.send(chat, "Укажите номер страницы, например /list 2.")
                return
            page = int(arg or "1")
            words = self.store.words(page)
            if not words:
                self.send(chat, "На этой странице нет слов.")
                return
            navigation = f"\n\nСледующая страница: /list {page + 1}" if len(words) == 10 else ""
            self.send(chat, format_words(words) + navigation)
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
                count = self.store.add(parse_words(content))
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
        if not chats:
            return
        words = self.store.words(review=True)
        if not words:
            return
        for chat in chats:
            if monotonic() < self.review_retry.get(chat, 0):
                continue
            try:
                self.send(chat, "Повторение на сегодня\n\n" + format_words(words))
            except TelegramError as error:
                if error.code == 403:
                    self.store.subscribe(chat, False)
                else:
                    self.review_retry[chat] = monotonic() + max(60, error.retry_after)
                    logger.warning("Рассылка: %s", error)
                if error.code == 429:
                    raise
            else:
                self.store.mark_review(chat, day)
                self.review_retry.pop(chat, None)

    def run(self):
        offset = int(self.store.setting("offset", "0"))
        registered = False
        while True:
            try:
                if not registered:
                    self.api.call("setMyCommands", commands=[
                        {"command": key, "description": label} for key, label in COMMANDS.items()
                    ])
                    registered = True
                self.review(datetime.now(UTC))
                updates = self.api.call("getUpdates", offset=offset, timeout=10,
                                        allowed_updates=["message"])
                for update in updates:
                    if "message" in update:
                        try:
                            self.handle(update["message"])
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
