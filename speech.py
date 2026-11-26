import os
import tempfile
from functools import lru_cache


@lru_cache(maxsize=1)
def _get_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        os.getenv("BOT_STT_MODEL", "large-v3"),
        device=os.getenv("BOT_STT_DEVICE", "cpu"),
        compute_type=os.getenv("BOT_STT_COMPUTE_TYPE", "int8"),
    )


def transcribe(audio):
    language = os.getenv("BOT_STT_LANGUAGE", "").strip().lower()
    if language and language not in {"ru", "en"}:
        raise ValueError("BOT_STT_LANGUAGE должен быть ru, en или пустым для автодетекции.")

    with tempfile.NamedTemporaryFile(suffix=".ogg") as source:
        source.write(audio)
        source.flush()
        options: dict[str, object] = {"vad_filter": True}
        if language:
            options["language"] = language
        segments, _ = _get_model().transcribe(source.name, **options)
        return " ".join(segment.text.strip() for segment in segments).strip()
