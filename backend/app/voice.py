"""Speech to text for voice messages, by any OpenAI-compatible transcription server.

The default is a Whisper server on this machine (WHISPER_URL, e.g. http://127.0.0.1:9000/v1
for the hwdsl2/whisper-server container), so what you say isn't sent anywhere. Replies are
read aloud by the app itself (the phone's or browser's own speech), not by the server.
"""

from __future__ import annotations

import os

import httpx

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # about 10 minutes of compressed speech
EXTENSIONS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
              "audio/aac": "aac", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/3gpp": "3gp"}

_transport: httpx.BaseTransport | None = None  # tests swap in a mock


class VoiceError(RuntimeError):
    """Transcription isn't set up or failed; the message is safe to show."""


def configured() -> bool:
    return bool(os.getenv("WHISPER_URL", "").strip())


def transcribe(audio: bytes, content_type: str) -> str:
    base = os.getenv("WHISPER_URL", "").strip().rstrip("/")
    if not base:
        raise VoiceError("Voice input needs a speech-to-text server: set WHISPER_URL (see README).")
    if not audio:
        raise VoiceError("No audio was received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("That recording is too long; keep voice messages under about 10 minutes.")
    mime = (content_type or "audio/webm").split(";")[0].strip().lower()
    filename = f"voice.{EXTENSIONS.get(mime, 'webm')}"
    key = os.getenv("WHISPER_API_KEY", "").strip()
    try:
        with httpx.Client(timeout=300, transport=_transport) as http:
            resp = http.post(
                f"{base}/audio/transcriptions",
                files={"file": (filename, audio, mime)},
                data={"model": os.getenv("WHISPER_MODEL", "whisper-1"), "response_format": "json"},
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
    except httpx.HTTPError as exc:
        raise VoiceError(f"The speech-to-text server didn't answer ({type(exc).__name__}); is it running?") from None
    if resp.status_code >= 400:
        raise VoiceError(f"The speech-to-text server returned {resp.status_code}.")
    text = (resp.json().get("text") or "").strip()
    if not text:
        raise VoiceError("Nothing was heard in that recording.")
    return text
