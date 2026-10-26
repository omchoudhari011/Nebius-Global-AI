"""A second "Hey Jarvis" detector, trained on top of openWakeWord's own speech features, for what the
stock model gets wrong: it barely reacts to an Indian accent (or "हे जार्विस" said by a Hindi
speaker), and it takes "Hey Travis" and "Hey service" for the wake word.

It reads the same 16 x 96 features the stock model does at each 80 ms step (no second pass over the
audio) and gives a probability; heard() combines the two. It was trained on synthetic speech from
200 voices - 160 English speakers, British and American voices, and Indian voices saying it in
English and in Hindi - under noise and across a room, against lookalikes and everyday requests, and
measured on voices it never heard (see README "How Jarvis hears"). The weights are in
models/hey_jarvis_plus.npz: a small network, run here with numpy.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

WEIGHTS = Path(__file__).with_name("models") / "hey_jarvis_plus.npz"
# The decision, as measured on held-out voices: the stock model's yes counts when this one doesn't
# object (that's what stops "Hey Travis"), and this one alone counts when it's sure (that's what
# hears an Indian accent).
AGREE = 0.2
ALONE = 0.95


class SecondOpinion:
    def __init__(self, path: Path = WEIGHTS) -> None:
        data = np.load(path)
        count = len([k for k in data.files if k.startswith("w")])
        self.layers = [(data[f"w{i}"], data[f"b{i}"]) for i in range(count)]

    def probability(self, features: np.ndarray) -> float:
        """features: (16, 96) or (1, 16, 96) - openWakeWord's preprocessor.get_features(16)."""
        x = np.asarray(features, dtype=np.float32).reshape(1, -1)
        for i, (w, b) in enumerate(self.layers):
            x = x @ w + b
            x = np.maximum(x, 0) if i < len(self.layers) - 1 else 1 / (1 + np.exp(-x))
        return float(x[0, 0])


def heard(stock: float, ours: float | None, threshold: float = 0.5) -> bool:
    """Whether this 80 ms step is the wake word. Without the second opinion, the stock model's
    score against its threshold, as before."""
    if ours is None:
        return stock >= threshold
    return (stock >= threshold and ours >= AGREE) or ours >= ALONE


class Detector:
    """A wake phrase of Custodian's own - "Hey Vitrus" - with no openWakeWord model behind it: its network
    (models/<name>.npz, from voice-lab/wake_vitrus_train.py) reads openWakeWord's speech features at each
    80 ms step, and it wakes when `run` steps in a row score `threshold` or more (the file says which)."""

    STRICT = 0.98  # while Custodian is speaking: its own voice mustn't wake it

    def __init__(self, path: Path, features) -> None:  # noqa: ANN001 - openwakeword.utils.AudioFeatures
        data = np.load(path)
        self.net = SecondOpinion(path)
        self.threshold = float(data["threshold"]) if "threshold" in data.files else 0.9
        self.run = int(data["run"]) if "run" in data.files else 2
        self.features, self.streak = features, 0

    def probability(self, frame: np.ndarray) -> float:
        self.features(frame)
        return self.net.probability(self.features.get_features(16))

    def heard(self, frame: np.ndarray, *, strict: bool = False) -> bool:
        threshold = max(self.threshold, self.STRICT) if strict else self.threshold
        self.streak = self.streak + 1 if self.probability(frame) >= threshold else 0
        if self.streak >= self.run:
            self.streak = 0
            self.features.reset()  # its features look back ~2 s: the same "Hey Vitrus" mustn't wake it twice
            return True
        return False


def own(name: str) -> Path | None:
    """The weights of Custodian's own wake phrase called `name` ("hey_vitrus"), or None."""
    if not name.isidentifier():  # a path to a model of your own, say
        return None
    path = WEIGHTS.with_name(f"{name}.npz")
    return path if path.exists() else None


def load() -> SecondOpinion | None:
    try:
        return SecondOpinion() if WEIGHTS.exists() else None
    except Exception:  # noqa: BLE001 - damaged weights: the stock model alone, as before
        return None
