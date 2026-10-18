"""Voice ID: only your voice can control the PC.

You record three short phrases once (tray → "Set up voice ID"); each spoken request is then
compared with that voiceprint before anything is sent to the server, and one that doesn't sound
like you is refused right here. It runs on this PC: a speaker-embedding model (WeSpeaker's
ResNet34, ~25 MB, onnxruntime - already used for the wake word) turns a recording into a vector,
and requests are compared by cosine similarity. The voiceprint (a list of numbers, not a
recording) is kept in %APPDATA%\\Custodian\\voiceid.

It fails closed: with voice ID on, a request is refused if the model or voiceprint can't be
loaded. It is a lock against other people talking to your PC, not against a recording of you:
keep the risky actions' "Allow once" for that.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import numpy as np

from . import config

RATE = 16_000
DIR = config.APP_DIR / "voiceid"
MODEL_FILE = DIR / "voxceleb_resnet34_LM.onnx"
MODEL_URL = "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx"
PRINT_FILE = DIR / "voiceprint.npy"
# Cosine similarity to the voiceprint. Measured on LibriSpeech test-clean (40 speakers, enrolled from
# 3 utterances, 3-second requests): the same speaker scored median 0.77, others median 0.08; at
# 0.45 the owner was refused 1.1% of the time and someone else let through 0.11%. A real microphone
# and room lower the owner's scores a little: re-record the voice ID if you're refused often.
THRESHOLD = 0.45
MIN_SECONDS = 1.0  # shorter than this says too little about a voice to judge
PHRASES = (
    "Hey Jarvis, what's on my calendar today?",
    "Play some music and turn the volume down a little.",
    "Remind me to call my mother at six in the evening.",
)


class VoiceIdUnavailable(RuntimeError):
    """The model or the voiceprint can't be loaded; with voice ID on, nothing is allowed."""


def _mel_banks(bins: int, fft: int, rate: int, low: float = 20.0) -> np.ndarray:
    """Kaldi's triangular mel filters, (bins, fft // 2 + 1)."""
    def mel(f):  # noqa: ANN001, ANN202
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    high = rate / 2
    edges = np.linspace(mel(low), mel(high), bins + 2)
    freqs = mel(np.arange(fft // 2 + 1) * rate / fft)
    left, centre, right = edges[:-2, None], edges[1:-1, None], edges[2:, None]
    up = (freqs - left) / (centre - left)
    down = (right - freqs) / (right - centre)
    return np.maximum(0.0, np.minimum(up, down))


_BANKS = _mel_banks(80, 512, RATE)


def fbank(samples: np.ndarray) -> np.ndarray:
    """80 log mel filterbank energies per 10 ms (25 ms Hamming windows), as Kaldi and WeSpeaker
    compute them, with the mean over the recording taken off. `samples` are 16 kHz int16."""
    x = samples.astype(np.float32)
    length, shift = 400, 160
    if len(x) < length:
        raise ValueError("too short")
    count = 1 + (len(x) - length) // shift
    frames = np.lib.stride_tricks.sliding_window_view(x, length)[::shift][:count].astype(np.float64)
    frames = frames - frames.mean(axis=1, keepdims=True)  # remove the DC offset
    frames = np.concatenate([frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)  # pre-emphasis
    frames *= np.hamming(length)
    power = np.abs(np.fft.rfft(frames, n=512)) ** 2
    feats = np.log(np.maximum(power @ _BANKS.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0)).astype(np.float32)


class Embedder:
    """The speaker model: a recording in, a unit vector out."""

    def __init__(self, path: Path = MODEL_FILE) -> None:
        if not path.exists():
            raise VoiceIdUnavailable(f"the voice ID model isn't installed ({path.name}); run run-desktop.ps1 -VoiceId")
        import onnxruntime

        self.session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        vector = self.session.run(None, {self.input: fbank(samples)[None]})[0][0]
        return vector / (np.linalg.norm(vector) or 1.0)


class VoiceId:
    def __init__(self, embedder: Callable[[], Callable[[np.ndarray], np.ndarray]] = Embedder, path: Path = PRINT_FILE,
                 threshold: float = THRESHOLD) -> None:
        self._make, self.path, self.threshold = embedder, path, threshold
        self._embed = None

    def _embedder(self) -> Callable[[np.ndarray], np.ndarray]:
        if self._embed is None:
            try:
                self._embed = self._make()
            except VoiceIdUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken model file, onnxruntime missing
                raise VoiceIdUnavailable(f"the voice ID model couldn't be loaded: {exc}") from None
        return self._embed

    def enrolled(self) -> bool:
        return self.path.exists()

    def enroll(self, recordings: list[np.ndarray]) -> None:
        usable = [r for r in recordings if len(r) >= RATE * MIN_SECONDS]
        if len(usable) < 2:
            raise ValueError("speak each phrase for at least a second")
        vectors = np.stack([self._embedder()(r) for r in usable])
        voiceprint = vectors.mean(axis=0)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.path, voiceprint / np.linalg.norm(voiceprint))

    def forget(self) -> None:
        if self.path.exists():
            os.remove(self.path)

    def score(self, samples: np.ndarray) -> float:
        if not self.enrolled():
            raise VoiceIdUnavailable("voice ID is on but no voice is set up; use the tray's “Set up voice ID”")
        try:
            voiceprint = np.load(self.path)
        except (OSError, ValueError) as exc:
            raise VoiceIdUnavailable(f"the voiceprint couldn't be read: {exc}") from None
        return float(np.dot(self._embedder()(samples), voiceprint))

    def is_owner(self, samples: np.ndarray) -> tuple[bool, float]:
        """(it's you, the similarity). Too short to tell counts as not you."""
        if len(samples) < RATE * MIN_SECONDS:
            return False, 0.0
        similarity = self.score(samples)
        return similarity >= self.threshold, similarity
