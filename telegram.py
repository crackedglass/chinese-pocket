import http.client
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VOICE_BYTES = 20 * 1024 * 1024


class TelegramError(Exception):
    def __init__(self, code=None, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"Ошибка Telegram ({code or 'сеть или формат ответа'})")


class Telegram:
    def __init__(self, token):
        self._token = token

    @staticmethod
    def _read(request, limit, timeout):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read(limit + 1)
        except HTTPError as error:
            with error:
                retry = error.headers.get("Retry-After", "")
                if error.code == 429 and not retry.isdecimal():
                    try:
                        retry = str(json.loads(error.read(8192))["parameters"]["retry_after"])
                    except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException):
                        retry = ""
                raise TelegramError(error.code, int(retry) if retry.isdecimal() else 0) from None
        except (URLError, OSError, http.client.HTTPException, ValueError):
            raise TelegramError() from None

    def call(self, method, **payload):
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        raw = self._read(request, 16 * 1024 * 1024, 20)
        try:
            data = json.loads(raw)
            if data["ok"]:
                return data["result"]
            raise TelegramError(data.get("error_code"), data.get("parameters", {}).get("retry_after", 0))
        except (ValueError, KeyError, TypeError):
            raise TelegramError() from None

    def photo(self, file_id):
        return self._download(file_id, MAX_PHOTO_BYTES)

    def voice(self, file_id):
        return self._download(file_id, MAX_VOICE_BYTES)

    def is_channel_admin(self, channel_id, user_id):
        try:
            member = self.call("getChatMember", chat_id=channel_id, user_id=user_id)
            return member.get("status") in {"creator", "administrator"}
        except TelegramError:
            return False

    def channel_admins(self, channel_id):
        try:
            return self.call("getChatAdministrators", chat_id=channel_id)
        except TelegramError:
            return []

    def _download(self, file_id, limit):
        file = self.call("getFile", file_id=file_id)
        if file.get("file_size", 0) > limit or not file.get("file_path"):
            raise TelegramError()
        path = quote(file["file_path"], safe="/")
        contents = self._read(
            f"https://api.telegram.org/file/bot{self._token}/{path}", limit, 15
        )
        if not contents or len(contents) > limit:
            raise TelegramError()
        return contents
