"""Listening and speaking.

Listener: the microphone streams 16 kHz audio into openWakeWord on this PC; when it hears the
wake word - "Hey Jarvis", or your own ("Hey Custodian": any openWakeWord model in
%APPDATA%\\Custodian\\wakewords) - or you press the hotkey, it records until you stop talking and
hands the WAV to a callback. Nothing is recorded or sent before the wake word.
- Faster: the moment you pause, what you said so far is handed to `on_early` (the app starts
  transcribing it); if you don't go on, that transcription is used and about half a second is saved.
- Barge-in: while Custodian is speaking it still listens for the wake word (a stricter score, so
  its own voice doesn't trigger it); saying it cuts the reply off and starts listening.

Speaker: replies are spoken with a Piper voice on this PC when one is installed in
%APPDATA%\\Custodian\\voices, otherwise with Windows' built-in voice - sentence by sentence, the
next one made while the last is playing, so it starts talking after the first sentence rather
than after the whole reply. Hindi and Marathi (Devanagari) get the Hindi voice when it's there.
"""

from __future__ import annotations

import io
import logging
import queue
import re
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from . import wake

log = logging.getLogger(__name__)

RATE = 16_000
FRAME = 1280  # 80 ms, what openWakeWord expects
MAX_SECONDS = 20
SILENCE_SECONDS = 1.0
EARLY_SILENCE = 0.45  # a pause this long starts transcribing what's been said so far
MIN_SECONDS = 0.6
BARGE_IN_SCORE = 0.75  # the wake word's score needed while Custodian is speaking
STALL_SECONDS = 4.0  # no sound at all for this long: the stream broke (sleep, a device change); open it again
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def to_wav(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue()


def from_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def _beep(freq: int, ms: int) -> None:
    try:
        import winsound

        winsound.Beep(freq, ms)
    except (ImportError, RuntimeError):
        pass


def _loudness(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


class Listener:
    """Runs on its own thread. on_speech(wav bytes, early) is called after the wake word or a
    trigger() - `early` is what on_early returned for the same words, or None; on_state(text)
    reports "listening", "recording", ... for the window."""

    def __init__(self, on_speech: Callable[..., None], on_state: Callable[[str], None], *,
                 wake_word: bool = True, threshold: float = 0.5, device: int | None = None,
                 wake_model: str = "hey_jarvis", on_early: Callable[[bytes], object] | None = None,
                 barge_in: bool = True, on_barge: Callable[[], None] | None = None,
                 on_level: Callable[[float], None] | None = None) -> None:
        self.on_speech, self.on_state, self.on_level = on_speech, on_state, on_level
        self.wake_word, self.threshold, self.device = wake_word, threshold, device
        self.wake_model, self.on_early, self.barge_in, self.on_barge = wake_model, on_early, barge_in, on_barge
        self._triggered = threading.Event()
        self._follow_up = threading.Event()  # this recording continues a conversation: no wake word needed
        self.follow_up_wait = 6.0
        self._paused = threading.Event()
        self._speaking = threading.Event()
        self._stop = threading.Event()
        self._model = None
        self._second = None  # wake.SecondOpinion, for the built-in "Hey Jarvis"
        self.early = None  # what on_early returned for the last recording's words, when they're all of them
        self._early_frames = 0

    def trigger(self) -> None:
        """Start recording now (the hotkey or the tray's "Talk now")."""
        self._triggered.set()

    def follow_up(self, seconds: float = 6.0) -> None:
        """Listen once more without the wake word - for `seconds` - so the conversation can go on.
        Saying nothing ends it quietly."""
        self.follow_up_wait = seconds
        self._follow_up.set()
        self._triggered.set()

    def pause(self, paused: bool) -> None:
        (self._paused.set if paused else self._paused.clear)()

    def speaking(self, speaking: bool) -> None:
        """Custodian is talking: hear only the wake word (for barge-in) or the hotkey."""
        (self._speaking.set if speaking else self._speaking.clear)()

    def stop(self) -> None:
        self._stop.set()

    def _load_model(self) -> None:
        if not self.wake_word or self._model is not None:
            return
        try:
            from openwakeword import utils
            from openwakeword.model import Model

            path = Path(self.wake_model)
            if path.suffix in (".onnx", ".tflite") and path.exists():  # your own wake word
                framework = "onnx" if path.suffix == ".onnx" else "tflite"
                self._model = Model(wakeword_models=[str(path)], inference_framework=framework)
            else:
                utils.download_models(model_names=[self.wake_model])
                self._model = Model(wakeword_models=[self.wake_model], inference_framework="onnx")
                if self.wake_model == "hey_jarvis":  # the second opinion was trained for this one (wake.py)
                    self._second = wake.load()
        except Exception as exc:  # noqa: BLE001 - keep working with the hotkey alone
            log.warning("wake word unavailable (%s); use the hotkey", exc)
            self.wake_word = False

    def heard_wake_word(self, frame: np.ndarray, speaking: bool) -> bool:
        if self._model is None:
            return False
        scores = self._model.predict(frame)
        score = max(scores.values()) if scores else 0.0
        threshold = max(BARGE_IN_SCORE, self.threshold) if speaking else self.threshold
        ours = None
        if self._second is not None and score >= 0.01:  # it only matters once the stock model stirs
            ours = self._second.probability(self._model.preprocessor.get_features(16))
        return wake.heard(score, ours, threshold) if ours is not None else score >= threshold

    def run(self) -> None:
        """Listen until stopped - and keep listening across what breaks a microphone stream: the PC
        sleeping and waking, a headset plugged in or out, a Bluetooth one dropping. When no sound
        has come for STALL_SECONDS the microphone is opened again (Windows' default one, as it is
        now); when it can't be opened, it's tried again every few seconds."""
        import sounddevice as sd

        self._load_model()
        failures = 0
        while not self._stop.is_set():
            try:
                self._listen(sd)
                failures = 0
            except Exception as exc:  # noqa: BLE001 - no microphone right now: wait, then try again
                failures += 1
                if failures in (1, 10):
                    log.warning("the microphone can't be used (%s); trying again", exc)
            if self._stop.is_set():
                break
            self._stop.wait(min(30.0, 2.0 * max(1, failures)))
            try:  # PortAudio keeps the device list it started with: read it again, to find the default one now
                sd._terminate()
                sd._initialize()
            except Exception:  # noqa: BLE001 - an older sounddevice: the next open still tries
                pass

    def _listen(self, sd) -> None:  # noqa: ANN001 - the sounddevice module
        """One microphone stream, until it goes quiet for STALL_SECONDS (or Custodian stops)."""
        frames: queue.Queue[np.ndarray] = queue.Queue()

        def callback(data, _frames, _time, status):  # noqa: ANN001
            if status:
                log.debug("audio: %s", status)
            frames.put(data[:, 0].copy())

        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, device=self.device, callback=callback):
            self.on_state("listening" if self.wake_word else "ready")
            noise = deque(maxlen=40)  # recent loudness, for the silence threshold
            last = time.monotonic()
            while not self._stop.is_set():
                try:
                    frame = frames.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() - last > STALL_SECONDS and not self._paused.is_set():
                        log.info("the microphone went quiet (the PC slept, or a device changed); opening it again")
                        return
                    continue
                last = time.monotonic()
                if self.step(frame, frames, noise):
                    self.on_state("listening" if self.wake_word else "ready")
                    last = time.monotonic()

    def step(self, frame: np.ndarray, frames: queue.Queue, noise: deque) -> bool:
        """One 80 ms frame: True when a recording was made (and handed on)."""
        if self._paused.is_set():
            self._triggered.clear()
            self._follow_up.clear()
            return False
        speaking = self._speaking.is_set()
        if speaking and not self.barge_in:
            self._triggered.clear()
            return False
        if not speaking:  # Custodian's own voice would raise the floor
            noise.append(_loudness(frame))
        heard = self.heard_wake_word(frame, speaking)
        if not (heard or self._triggered.is_set()):
            return False
        self._triggered.clear()
        if speaking and self.on_barge is not None:
            self.on_barge()  # cut the reply off; what follows is a new request
        floor = float(np.median(noise)) if noise else 200.0
        audio = self._record(frames, floor)
        if self._model is not None:
            self._model.reset()
        if audio is not None:
            if self.on_early is not None:
                self.on_speech(to_wav(audio), self.early)
            else:
                self.on_speech(to_wav(audio))
        return True

    def _record(self, frames: queue.Queue, floor: float) -> np.ndarray | None:
        following = self._follow_up.is_set()
        self._follow_up.clear()
        if following:
            _beep(1320, 45)  # a softer cue: still listening
        else:
            _beep(880, 120)
        self.on_state("following up" if following else "recording")
        for _ in range(min(frames.qsize(), 3)):  # drop the tail of the wake word, keep what follows
            frames.get_nowait()
        threshold = max(floor * 2.5, 350.0)
        chunks, quiet, started, spoke = [], 0.0, time.monotonic(), False
        self.early = None
        while time.monotonic() - started < MAX_SECONDS and not self._stop.is_set():
            try:
                frame = frames.get(timeout=1)
            except queue.Empty:
                break
            chunks.append(frame)
            volume = _loudness(frame)
            if self.on_level is not None:
                self.on_level(min(1.0, volume / 3000.0))
            loud = volume > threshold
            spoke = spoke or loud
            if loud:
                quiet = 0.0
                self.early = None  # they went on: what was transcribed early is out of date
            else:
                quiet += FRAME / RATE
            if spoke and quiet >= EARLY_SILENCE and self.early is None and self.on_early is not None:
                self.early = self.on_early(to_wav(np.concatenate(chunks)))
                self._early_frames = len(chunks)
            if spoke and quiet >= SILENCE_SECONDS:
                break
            if not spoke and time.monotonic() - started > (self.follow_up_wait if following else 6):  # nothing said
                break
        _beep(660, 90)
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        if self.on_level is not None:
            self.on_level(0.0)
        if not spoke or len(audio) < RATE * MIN_SECONDS:
            self.on_state("conversation over" if following and not spoke else "didn't catch that")
            self.early = None
            return None
        if self.early is not None:  # nothing loud since: the early words are all the words
            audio = audio[: self._early_frames * FRAME]
        return audio


def sentences(text: str) -> list[str]:
    """Pieces to speak one at a time: sentences, with very short ones joined to the next."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?।])\s+|\n+", text) if p.strip()]
    out: list[str] = []
    for part in parts:
        if out and len(out[-1]) < 25:
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


class Speaker:
    """Speaks one reply at a time; speaking again (or stop()) cuts the current one off."""

    def __init__(self, voices_dir: Path, voice: str, hindi_voice: str = "hi_IN-pratham-medium") -> None:
        self.voices_dir = voices_dir
        self._names = {"en": voice, "hi": hindi_voice}
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    @staticmethod
    def clean(text: str) -> str:
        """What's worth saying: no links, markdown or source lists."""
        text = re.sub(r"(?im)^\s*(sources?|links?)\s*:.*$", "", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"[*_`#>|]+", "", text)
        text = text.replace("Custodian check:", "Note:")
        return " ".join(text.split())

    def _piper(self, language: str):  # noqa: ANN202 - a PiperVoice, or None
        if language not in self._voices:
            model = self.voices_dir / f"{self._names[language]}.onnx"
            self._voices[language] = None
            if model.exists():
                try:
                    from piper import PiperVoice

                    self._voices[language] = PiperVoice.load(str(model))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Piper voice %s unavailable (%s)", model.name, exc)
        return self._voices[language]

    def say(self, text: str) -> None:
        text = self.clean(text)
        if not text:
            return
        self.stop()
        with self._lock:
            self._stop.clear()
            language = "hi" if DEVANAGARI.search(text) else "en"
            voice = self._piper(language)
            if voice is not None:
                self._say_piper(voice, sentences(text))
            else:
                self._say_windows(sentences(text), language)

    def _synthesize(self, voice, text: str) -> tuple[np.ndarray, int]:  # noqa: ANN001
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            voice.synthesize_wav(text, w)
        buffer.seek(0)
        with wave.open(buffer, "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()

    def _play(self, audio: np.ndarray, rate: int) -> None:
        import sounddevice as sd

        sd.play(audio, rate)
        while sd.get_stream().active and not self._stop.is_set():
            time.sleep(0.03)
        sd.stop()

    def _say_piper(self, voice, parts: list[str]) -> None:  # noqa: ANN001
        """Make the next sentence while this one plays."""
        ready: queue.Queue = queue.Queue(maxsize=2)

        def hand_over(item) -> bool:  # noqa: ANN001 - False once the reply was cut off
            while not self._stop.is_set():
                try:
                    ready.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def make() -> None:
            for part in parts:
                if self._stop.is_set():
                    return
                try:
                    if not hand_over(self._synthesize(voice, part)):
                        return
                except Exception as exc:  # noqa: BLE001
                    log.warning("couldn't speak %r: %s", part[:40], exc)
            hand_over(None)

        threading.Thread(target=make, daemon=True, name="speech").start()
        while not self._stop.is_set():
            try:
                item = ready.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            self._play(*item)

    def _say_windows(self, parts: list[str], language: str) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        if language == "hi":  # a Hindi voice, if Windows has one (Settings > Time & language > Speech)
            for v in engine.getProperty("voices"):
                if "hindi" in (v.name or "").lower() or "hi" in [str(x).lower()[:2] for x in (getattr(v, "languages", None) or [])]:
                    engine.setProperty("voice", v.id)
                    break
        for part in parts:
            if self._stop.is_set():
                break
            engine.say(part)
            engine.runAndWait()

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001
            pass
