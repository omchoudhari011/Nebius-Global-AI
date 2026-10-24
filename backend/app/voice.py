"""Speech to text for voice messages: in this process, or by any OpenAI-compatible server.

WHISPER_URL=local runs Whisper here (faster-whisper; pip install -r requirements-voice.txt), on
the graphics card when there is one: no Docker, nothing leaves the machine, and the model is
loaded on first use and let go after WHISPER_IDLE_MINUTES (default 10) without a recording, so
it doesn't hold memory while you aren't talking. Otherwise WHISPER_URL is a transcription server,
e.g. http://127.0.0.1:9000/v1 for the hwdsl2/whisper-server container. Replies are read aloud
by the app itself (the phone's or browser's own speech, the desktop app's Piper voice).
"""

from __future__ import annotations

import gc
import glob
import io
import logging
import os
import sys
import threading

import httpx

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # about 10 minutes of compressed speech
EXTENSIONS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
              "audio/aac": "aac", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/3gpp": "3gp"}

_transport: httpx.BaseTransport | None = None  # tests swap in a mock


class VoiceError(RuntimeError):
    """Transcription isn't set up or failed; the message is safe to show."""


def _url() -> str:
    return os.getenv("WHISPER_URL", "").strip()


def configured() -> bool:
    return bool(_url())


def local() -> bool:
    return _url().lower() == "local"


LANGUAGES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
SPOKEN = tuple(LANGUAGES)  # the languages people speak to Custodian in: "any" means one of these
SAME_AS = {"ur": "hi"}  # spoken Hindi and Urdu are one speech; Whisper picks Urdu's script for it


def _check(audio: bytes) -> None:
    if not audio:
        raise VoiceError("No audio was received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("That recording is too long; keep voice messages under about 10 minutes.")


def transcribe(audio: bytes, content_type: str, language: str | None = None) -> str:
    """`language` ("en", "hi", "mr") tells Whisper what to expect; None lets it choose among those
    three. (A hint of typical commands and the user's names was tried, and measured worse - in noise
    and in Hindi Whisper drifts toward the hint's words: voice-lab/compare.py.)"""
    base = _url().rstrip("/")
    if not base:
        raise VoiceError("Voice input needs speech to text: set WHISPER_URL=local or a server's address (see README).")
    _check(audio)
    if local():
        text = LOCAL.transcribe(audio, language if language in LANGUAGES else None)
    else:
        text = _remote(base, audio, content_type, language)
    if not text:
        raise VoiceError("Nothing was heard in that recording.")
    return text


def _remote(base: str, audio: bytes, content_type: str, language: str | None) -> str:
    mime = (content_type or "audio/webm").split(";")[0].strip().lower()
    filename = f"voice.{EXTENSIONS.get(mime, 'webm')}"
    key = os.getenv("WHISPER_API_KEY", "").strip()
    try:
        with httpx.Client(timeout=300, transport=_transport) as http:
            resp = http.post(
                f"{base}/audio/transcriptions",
                files={"file": (filename, audio, mime)},
                data={"model": os.getenv("WHISPER_MODEL", "whisper-1"), "response_format": "json",
                      **({"language": language} if language in LANGUAGES else {})},
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
    except httpx.HTTPError as exc:
        raise VoiceError(f"The speech-to-text server didn't answer ({type(exc).__name__}); is it running?") from None
    if resp.status_code >= 400:
        raise VoiceError(f"The speech-to-text server returned {resp.status_code}.")
    return (resp.json().get("text") or "").strip()


# Whisper in this process ---------------------------------------------------------------------

def _cuda_libraries() -> None:
    """On Windows, the CUDA libraries come as pip packages (nvidia-cublas-cu12, nvidia-cudnn-cu12):
    let the speech model find them."""
    if sys.platform != "win32":
        return
    for site in {p for p in sys.path if p.endswith("site-packages")}:
        for folder in glob.glob(os.path.join(site, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(folder)
            except OSError:
                continue
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


class LocalWhisper:
    """One Whisper model, loaded when it's first needed and let go when it hasn't been for a while."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._timer: threading.Timer | None = None
        self.device = ""
        self.name = ""

    @staticmethod
    def idle_minutes() -> float:
        try:
            return float(os.getenv("WHISPER_IDLE_MINUTES", "10"))
        except ValueError:
            return 10.0

    def _load(self):  # noqa: ANN202 - a faster_whisper.WhisperModel
        if self._model is not None:
            return self._model
        _cuda_libraries()
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
        except ImportError:
            raise VoiceError("Speech to text on this machine needs faster-whisper: "
                             "pip install -r requirements-voice.txt") from None
        wanted = os.getenv("WHISPER_DEVICE", "auto").strip().lower()
        gpu = wanted == "cuda" or (wanted == "auto" and ctranslate2.get_cuda_device_count() > 0)
        self.device = "cuda" if gpu else "cpu"
        # The large model hears Hindi, Marathi and Hinglish far better, and on a graphics card it's
        # as quick as a small one; on the processor alone, the small one keeps replies quick.
        self.name = os.getenv("WHISPER_LOCAL_MODEL", "").strip() or ("large-v3-turbo" if gpu else "small")
        folder = os.getenv("WHISPER_MODEL_DIR", "").strip() or None  # None: the usual Hugging Face cache
        try:
            self._model = WhisperModel(self.name, device=self.device, compute_type="int8_float16" if gpu else "int8",
                                       download_root=folder)
        except Exception as exc:  # noqa: BLE001 - a missing driver, no disk space, no network for the first download
            log.warning("couldn't load Whisper %s on %s: %s", self.name, self.device, exc)
            raise VoiceError(f"The speech model couldn't be loaded ({type(exc).__name__}).") from None
        log.info("Whisper %s loaded on %s", self.name, self.device)
        return self._model

    def _let_go_later(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        minutes = self.idle_minutes()
        if minutes > 0:
            self._timer = threading.Timer(minutes * 60, self.unload)
            self._timer.daemon = True
            self._timer.start()

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            gc.collect()  # the model's memory, on the graphics card too, goes with the last reference
            log.info("Whisper unloaded after %g idle minutes", self.idle_minutes())

    def loaded(self) -> bool:
        return self._model is not None

    def preload(self) -> None:
        """Load it now ("Hey Jarvis" was heard), so the recording doesn't wait for the load."""
        try:
            with self._lock:
                self._load()
                self._let_go_later()
        except VoiceError as exc:
            log.debug("no Whisper preload: %s", exc)

    def transcribe(self, audio: bytes, language: str | None) -> str:
        with self._lock:
            model = self._load()
            try:
                from faster_whisper import decode_audio

                samples = decode_audio(io.BytesIO(audio))
                language = language or self.which_language(model, samples)
                # The VAD filter skips silence, where Whisper would otherwise "hear" a "Thank you."
                segments, _info = model.transcribe(samples, language=language, beam_size=5, vad_filter=True,
                                                   condition_on_previous_text=False)
                text = " ".join(s.text.strip() for s in segments).strip()
            except Exception as exc:  # noqa: BLE001 - audio it can't decode
                log.info("couldn't transcribe: %s", exc)
                raise VoiceError("That recording couldn't be read.") from None
            finally:
                self._let_go_later()
        return text

    @staticmethod
    def which_language(model, samples) -> str:  # noqa: ANN001
        """English, Hindi or Marathi - whichever Whisper finds likeliest of the three. Left to choose
        among all its languages, a short "open chrome" comes out Vietnamese or Portuguese, and Hindi in
        Urdu's script (the same speech): Urdu counts as Hindi here."""
        try:
            _lang, _p, ranked = model.detect_language(samples, vad_filter=True)
        except Exception:  # noqa: BLE001 - too short to tell: English is likeliest
            return "en"
        score = {code: 0.0 for code in SPOKEN}
        for code, probability in ranked:
            code = SAME_AS.get(code, code)
            if code in score:
                score[code] += probability
        return max(score, key=score.get)


LOCAL = LocalWhisper()
