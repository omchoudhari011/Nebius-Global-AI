"""Train the "Hey Vitrus" wake word: a small network on openWakeWord's own speech features (the 16 x 96
embedding frames it computes every 80 ms, with no wake model of its own), from synthetic speech.

"Hey Vitrus" (said VIT-rus) is spoken by 8 named voices - British, American and Indian, the Indian ones
in English and in Hindi - and 200 LibriTTS speakers, at four speeds, clean, in noise, across a room and
loud. It's learnt against lookalikes ("Hey citrus", "Hey virus", "Hey Titus", "Hey Jarvis" ...), the voice
lab's everyday spoken requests, and noise. Two named voices and 40 speakers are held out and measured:
how often the phrase wakes it, how often a lookalike does, and false wakes per hour of other speech
streamed end to end, as the app hears it.

Usage: wake_vitrus_train.py <out .npz> (then copy it to desktop/custodian_desktop/models/hey_vitrus.npz)
Needs voice-lab/voices (get_voices.ps1) and voice-lab/clips (build.py)."""
import json
import random
import sys
import time
import wave
from pathlib import Path

import numpy as np
from openwakeword.utils import AudioFeatures

import build
from wake_train import HELD_OUT, LIBRITTS, NAMED, dress, say

HERE = Path(__file__).parent
FRAME = 1280
rng = random.Random(23)
WAKE_EN = ["Hey Vitrus", "Hey, Vitrus!", "Hey Vitrus.", "Hey Vittrus", "Hey Vitrus?"]
WAKE_HI = ["हे विट्रस", "हेय विट्रस", "Hey Vitrus"]
NEAR = ["Hey citrus", "Hey virus", "Hey Titus", "Hey Cyrus", "Hey Travis", "Hey Jarvis", "Hey Vitamin", "Hey Victor",
        "Hey Petrus", "Hey Brutus", "Hey it was", "Hey with us", "Hey Ritu", "Hey Mitra", "Hey Venus", "Hey Vitara",
        "Hey Victoria", "Hey Vikram", "Hey Vidya", "Hey fitness", "Hey Peter", "Hey Siri", "OK Google", "Hey Alexa",
        "Hey guys", "Hey there", "Vitamins", "Citrus fruits", "The virus", "Very true", "Hey Vitruvius", "Hey Nitesh",
        "Hey, it's us", "Hey Rufus", "Hey Lettuce", "With us", "Hey Pirates", "Hey Vitor"]
NEAR_HI = ["हे भगवान", "हे राम", "विटामिन", "हे वीरू", "हे ट्रैविस", "चित्रा"]
SPEAKERS = list(range(904))
rng.shuffle(SPEAKERS)
TRAIN_SPEAKERS, TEST_SPEAKERS = SPEAKERS[:160], SPEAKERS[160:200]


def stream(audio: np.ndarray, features: AudioFeatures) -> np.ndarray:
    """The 16 x 96 features at each 80 ms step, as the app computes them live."""
    features.reset()
    audio = np.clip(np.concatenate([np.zeros(FRAME * 6), audio, np.zeros(FRAME * 4)]), -32000, 32000).astype(np.int16)
    out = []
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        features(audio[i:i + FRAME])
        out.append(features.get_features(16)[0].astype(np.float16))
    return np.array(out)


def clips() -> list[dict]:
    out = []

    def add(audio, wake, voice, test, how, text, **extra):
        out.append({"audio": audio, "wake": wake, "voice": voice, "test": test, "how": how, "text": text, **extra})

    for voice in NAMED:
        hindi, test = voice.startswith("hi_"), voice in HELD_OUT
        for text in (WAKE_HI if hindi else WAKE_EN):
            for speed in (0.85, 1.0, 1.15, 1.3):
                for how in ("clean", "noisy", "far", "loud"):
                    spoken = say(voice, text, speed)
                    # where the phrase ends in the streamed audio: 6 frames of lead-in, then pad()'s 0.25 s
                    add(dress(spoken, how), True, voice, test, how, text, end=FRAME * 6 + int(0.25 * 16_000) + len(spoken))
        for text in NEAR + (NEAR_HI if hindi else []):
            for how in ("clean", "noisy", "far"):
                add(dress(say(voice, text, rng.choice((0.9, 1.0, 1.2))), how), False, voice, test, how, text, near=True)
    for group, speakers in (("train", TRAIN_SPEAKERS), ("test", TEST_SPEAKERS)):
        for speaker in speakers:
            for text in rng.sample(WAKE_EN, 2):
                spoken = say(LIBRITTS, text, rng.uniform(0.85, 1.3), speaker)
                add(dress(spoken, rng.choice(("clean", "noisy", "far"))), True, f"libritts-{speaker}", group == "test",
                    "mixed", text, end=FRAME * 6 + int(0.25 * 16_000) + len(spoken))
            for text in rng.sample(NEAR, 4):
                add(dress(say(LIBRITTS, text, 1.0, speaker), rng.choice(("clean", "noisy", "far"))), False,
                    f"libritts-{speaker}", group == "test", "mixed", text, near=True)
    index = json.loads((HERE / "clips" / "index.json").read_text(encoding="utf-8"))
    for item in index:
        with wave.open(str(HERE / "clips" / item["file"])) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        add(audio, False, item["voice"], item["voice"] in HELD_OUT, item["how"], item["text"], speech=True)
    for k in range(60):
        n = 16_000 * 3
        add(build.brown(n) * 300 + build.babble(n) * rng.uniform(0, 1), False, "noise", k % 4 == 0, "noise", "(noise)")
    return out


def wakes(probs: np.ndarray, threshold: float, run: int) -> int:
    """How many times it would wake: `run` steps in a row at or above the threshold, then 2 s of quiet."""
    count = streak = 0
    hold = 0
    for p in probs:
        if hold:
            hold -= 1
            continue
        streak = streak + 1 if p >= threshold else 0
        if streak >= run:
            count, streak, hold = count + 1, 0, 25
    return count


def main() -> None:
    started = time.time()
    features = AudioFeatures(inference_framework="onnx")
    data = clips()
    print(f"{len(data)} clips made in {time.time() - started:.0f} s", flush=True)
    for item in data:
        item["feats"] = stream(item["audio"], features)
        item["seconds"] = len(item["audio"]) / 16_000
        del item["audio"]
    print(f"streamed in {time.time() - started:.0f} s", flush=True)

    # Wake steps: the ones whose window holds the whole phrase - from where it ends to ~0.3 s after.
    # Not-wake steps: every other step of the rest, and a wake clip's steps before the phrase is half said.
    X, y = [], []
    for item in data:
        if item["test"]:
            continue
        feats = item["feats"].reshape(len(item["feats"]), -1)
        if item["wake"]:
            end = item["end"] // FRAME
            for i in range(end, min(len(feats), end + 4)):
                X.append(feats[i])
                y.append(1)
            for i in range(0, max(0, end - 8), 2):
                X.append(feats[i])
                y.append(0)
        else:
            for i in range(0, len(feats), 2):
                X.append(feats[i])
                y.append(0)
    X, y = np.array(X, dtype=np.float32), np.array(y)
    print(f"training on {len(y)} steps: {y.sum()} wake, {len(y) - y.sum()} not", flush=True)

    from sklearn.neural_network import MLPClassifier

    pos = np.where(y == 1)[0]
    idx = np.concatenate([np.arange(len(y)), np.repeat(pos, max(1, (len(y) - len(pos)) // len(pos) // 3))])
    net = MLPClassifier(hidden_layer_sizes=(128, 32), alpha=3e-3, max_iter=80, early_stopping=True, random_state=3,
                        batch_size=256, learning_rate_init=1e-3)
    net.fit(X[idx], y[idx])
    print(f"trained in {time.time() - started:.0f} s", flush=True)
    np.savez_compressed(sys.argv[1], **{f"w{i}": w.astype(np.float32) for i, w in enumerate(net.coefs_)},
                        **{f"b{i}": b.astype(np.float32) for i, b in enumerate(net.intercepts_)})

    test = [d for d in data if d["test"]]
    for item in test:
        item["probs"] = net.predict_proba(item["feats"].reshape(len(item["feats"]), -1).astype(np.float32))[:, 1]
    wake = [d for d in test if d["wake"]]
    near = [d for d in test if not d["wake"] and d.get("near")]
    other = [d for d in test if not d["wake"] and not d.get("near")]
    hours = sum(d["seconds"] for d in other) / 3600
    print(f"\nheld out: {len(wake)} wake, {len(near)} lookalikes, {len(other)} other speech and noise ({hours * 60:.0f} min)")
    for run in (1, 2, 3):
        for t in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98):
            woke = 100 * np.mean([wakes(d["probs"], t, run) > 0 for d in wake])
            fooled = 100 * np.mean([wakes(d["probs"], t, run) > 0 for d in near])
            false = sum(wakes(d["probs"], t, run) for d in other) / max(hours, 1e-9)
            print(f"  {run} step(s) >= {t:.2f}: wakes {woke:5.1f}%   lookalikes {fooled:5.1f}%   other speech {false:5.1f}/hour")
    by_voice: dict = {}
    for d in wake:
        key = d["voice"] if not d["voice"].startswith("libritts") else "libritts (40 new speakers)"
        by_voice.setdefault(key, []).append(d)
    print("\nheld-out voices, 2 steps >= 0.9:")
    for v, rows in by_voice.items():
        print(f"  {v:28s} {100 * np.mean([wakes(d['probs'], 0.9, 2) > 0 for d in rows]):5.1f}%")
    worst = sorted(near, key=lambda d: -float(d["probs"].max()))[:8]
    print("\nlookalikes it likes most:", ", ".join(f"{d['text']} ({d['probs'].max():.2f})" for d in worst))


if __name__ == "__main__":
    main()
