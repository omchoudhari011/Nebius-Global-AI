"""Listening and speaking.

Listener: the microphone streams 16 kHz audio into openWakeWord on this PC; when it hears
"Hey Jarvis" (or you press the hotkey), it records until you stop talking - about a second of
quiet - and hands the WAV to a callback. Nothing is recorded or sent before the wake word.

Speaker: replies are spoken with a Piper voice on this PC when one is installed in
%APPDATA%\\Custodian\\voices, otherwise with Windows' built-in voice.
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

log = logging.getLogger(__name__)

RATE = 16_000
FRAME = 1280  # 80 ms, what openWakeWord expects
MAX_SECONDS = 20
SILENCE_SECONDS = 1.0
MIN_SECONDS = 0.6


def to_wav(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue()


def _beep(freq: int, ms: int) -> None:
    try:
        import winsound

        winsound.Beep(freq, ms)
    except (ImportError, RuntimeError):
        pass


class Listener:
    """Runs on its own thread. on_speech(wav bytes) is called after the wake word or a
    trigger(); on_state(text) reports "listening", "recording", ... for the window."""

    def __init__(self, on_speech: Callable[[bytes], None], on_state: Callable[[str], None], *,
                 wake_word: bool = True, threshold: float = 0.5, device: int | None = None) -> None:
        self.on_speech, self.on_state = on_speech, on_state
        self.wake_word, self.threshold, self.device = wake_word, threshold, device
        self._triggered = threading.Event()
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._model = None

    def trigger(self) -> None:
        """Start recording now (the hotkey or the tray's "Talk now")."""
        self._triggered.set()

    def pause(self, paused: bool) -> None:
        (self._paused.set if paused else self._paused.clear)()

    def stop(self) -> None:
        self._stop.set()

    def _load_model(self) -> None:
        if not self.wake_word or self._model is not None:
            return
        try:
            from openwakeword import utils
            from openwakeword.model import Model

            utils.download_models(model_names=["hey_jarvis"])
            self._model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception as exc:  # noqa: BLE001 - keep working with the hotkey alone
            log.warning("wake word unavailable (%s); use the hotkey", exc)
            self.wake_word = False

    def run(self) -> None:
        import sounddevice as sd

        self._load_model()
        frames: queue.Queue[np.ndarray] = queue.Queue()

        def callback(data, _frames, _time, status):  # noqa: ANN001
            if status:
                log.debug("audio: %s", status)
            frames.put(data[:, 0].copy())

        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, device=self.device, callback=callback):
            self.on_state("listening" if self.wake_word else "ready")
            noise = deque(maxlen=40)  # recent loudness, for the silence threshold
            while not self._stop.is_set():
                try:
                    frame = frames.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self._paused.is_set():
                    self._triggered.clear()
                    continue
                noise.append(float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))))
                heard = False
                if self._model is not None:
                    score = self._model.predict(frame).get("hey_jarvis", 0.0)
                    heard = score >= self.threshold
                if heard or self._triggered.is_set():
                    self._triggered.clear()
                    floor = float(np.median(noise)) if noise else 200.0
                    audio = self._record(frames, floor)
                    if self._model is not None:
                        self._model.reset()
                    if audio is not None:
                        self.on_speech(to_wav(audio))
                    self.on_state("listening" if self.wake_word else "ready")

    def _record(self, frames: queue.Queue, floor: float) -> np.ndarray | None:
        _beep(880, 120)
        self.on_state("recording")
        for _ in range(min(frames.qsize(), 3)):  # drop the tail of the wake word, keep what follows
            frames.get_nowait()
        threshold = max(floor * 2.5, 350.0)
        chunks, quiet, started, spoke = [], 0.0, time.monotonic(), False
        while time.monotonic() - started < MAX_SECONDS and not self._stop.is_set():
            try:
                frame = frames.get(timeout=1)
            except queue.Empty:
                break
            chunks.append(frame)
            loud = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) > threshold
            spoke = spoke or loud
            quiet = 0.0 if loud else quiet + FRAME / RATE
            if spoke and quiet >= SILENCE_SECONDS:
                break
            if not spoke and time.monotonic() - started > 6:  # nothing said after the wake word
                break
        _beep(660, 90)
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        if not spoke or len(audio) < RATE * MIN_SECONDS:
            self.on_state("didn't catch that")
            return None
        return audio


class Speaker:
    """Speaks one reply at a time; speaking again (or stop()) cuts the current one off."""

    def __init__(self, voices_dir: Path, voice: str) -> None:
        self._piper = None
        model = voices_dir / f"{voice}.onnx"
        if model.exists():
            try:
                from piper import PiperVoice

                self._piper = PiperVoice.load(str(model))
            except Exception as exc:  # noqa: BLE001
                log.warning("Piper voice unavailable (%s); using the Windows voice", exc)
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

    def say(self, text: str) -> None:
        text = self.clean(text)
        if not text:
            return
        self.stop()
        with self._lock:
            self._stop.clear()
            if self._piper is not None:
                self._say_piper(text)
            else:
                self._say_windows(text)

    def _say_piper(self, text: str) -> None:
        import sounddevice as sd

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            self._piper.synthesize_wav(text, w)
        buffer.seek(0)
        with wave.open(buffer, "rb") as w:
            rate = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        sd.play(audio, rate)
        while sd.get_stream().active and not self._stop.is_set():
            time.sleep(0.05)
        sd.stop()

    def _say_windows(self, text: str) -> None:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001
            pass
