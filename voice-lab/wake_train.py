"""Train a second "Hey Jarvis" detector on top of openWakeWord's own speech features, for the voices
the stock model misses (an Indian accent, Hindi speakers) and the lookalikes it takes ("Hey Travis").

Every clip is streamed 80 ms at a time through the stock model, as the desktop app does, and the
features it has at each step (16 frames x 96) are what the new detector learns from. Voices and
speakers held out of training measure it. Usage: wake_train.py <out .npz> (then copy it to
desktop/custodian_desktop/models/hey_jarvis_plus.npz)"""
import random
import sys
import time
from pathlib import Path

import numpy as np
from openwakeword.model import Model
from piper import PiperVoice, SynthesisConfig

import build

HERE = Path(__file__).parent
FRAME = 1280
rng = random.Random(11)
np_rng = np.random.default_rng(11)

NAMED = ["en_GB-alan-medium", "en_US-lessac-medium", "en_US-amy-medium", "en_GB-jenny_dioco-medium", "en_US-ryan-medium",
         "hi_IN-pratham-medium", "hi_IN-rohan-medium", "hi_IN-priyamvada-medium"]
HELD_OUT = {"en_US-amy-medium", "hi_IN-priyamvada-medium"}
WAKE_EN = ["Hey Jarvis", "Hey, Jarvis!", "Hey Jarvis.", "Hey Jarvis?"]
WAKE_HI = ["हे जार्विस", "हेय जार्विस", "Hey Jarvis"]
NEAR = ["Hey Travis", "Hey service", "Hey Harvey", "Hey Marcus", "Hey Davis", "Hey Jasmine", "Hey Ravi", "Hey Charvi",
        "Jars of jam", "Harvest season", "Hey Siri", "OK Google", "Hey there", "Hey Alexa", "Nervous", "Hey Jarvin",
        "Service center", "Hey Garvit", "Hey Java", "Hey guys", "Hey Travis, come here", "That's Harvey's"]
NEAR_HI = ["हे राम", "हे भगवान", "हे सर्विस", "हे ट्रैविस", "चार बिस्किट", "जा भी"]
LIBRITTS = "en_US-libritts_r-medium"
SPEAKERS = list(range(904))
rng.shuffle(SPEAKERS)
TRAIN_SPEAKERS, TEST_SPEAKERS = SPEAKERS[:160], SPEAKERS[160:200]
_voices: dict = {}


def say(voice: str, text: str, speed: float, speaker: int | None = None) -> np.ndarray:
    import io
    import wave

    if voice not in _voices:
        _voices[voice] = PiperVoice.load(str(HERE / "voices" / f"{voice}.onnx"))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        _voices[voice].synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1 / speed, speaker_id=speaker))
    buf.seek(0)
    with wave.open(buf) as w:
        rate, audio = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if rate != 16_000:
        audio = np.interp(np.arange(0, len(audio), rate / 16_000), np.arange(len(audio)), audio)
    return audio


def dress(audio: np.ndarray, how: str) -> np.ndarray:
    audio = build.pad(audio)
    if how == "noisy":
        audio = build.at_snr(audio, 0.6 * build.babble(len(audio)) + build.brown(len(audio)), rng.uniform(6, 14))
    elif how == "far":
        audio = build.at_snr(build.laptop_mic(audio), build.brown(len(audio)), rng.uniform(10, 20))
    elif how == "loud":
        audio = audio * 1.8
    return audio


def stream(audio: np.ndarray, model: Model) -> tuple[np.ndarray, np.ndarray]:
    """(stock score, features) at each 80 ms step, as the app sees them live."""
    model.reset()
    audio = np.clip(np.concatenate([np.zeros(FRAME * 6), audio, np.zeros(FRAME * 4)]), -32000, 32000).astype(np.int16)
    scores, feats = [], []
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        scores.append(max(model.predict(audio[i:i + FRAME]).values()))
        feats.append(model.preprocessor.get_features(16)[0].astype(np.float16))
    return np.array(scores, dtype=np.float32), np.array(feats)


def clips() -> list[dict]:
    """Every clip: its audio and what it is (wake word or not, which voice, train or test)."""
    out = []
    for voice in NAMED:
        hindi = voice.startswith("hi_")
        test = voice in HELD_OUT
        for text in (WAKE_HI if hindi else WAKE_EN):
            for speed in (0.85, 1.0, 1.15, 1.3):
                for how in ("clean", "noisy", "far", "loud"):
                    out.append({"audio": dress(say(voice, text, speed), how), "wake": True, "voice": voice, "test": test,
                                "how": how, "text": text})
        for text in (NEAR + (NEAR_HI if hindi else [])):
            for how in ("clean", "noisy", "far"):
                out.append({"audio": dress(say(voice, text, rng.choice((0.9, 1.0, 1.2))), how), "wake": False,
                            "voice": voice, "test": test, "how": how, "text": text, "near": True})
    for group, speakers in (("train", TRAIN_SPEAKERS), ("test", TEST_SPEAKERS)):
        for speaker in speakers:
            for text in rng.sample(WAKE_EN, 2):
                out.append({"audio": dress(say(LIBRITTS, text, rng.uniform(0.85, 1.3), speaker), rng.choice(("clean", "noisy", "far"))),
                            "wake": True, "voice": f"libritts-{speaker}", "test": group == "test", "how": "mixed", "text": text})
            for text in rng.sample(NEAR, 2):
                out.append({"audio": dress(say(LIBRITTS, text, 1.0, speaker), rng.choice(("clean", "noisy", "far"))),
                            "wake": False, "voice": f"libritts-{speaker}", "test": group == "test", "how": "mixed",
                            "text": text, "near": True})
    # Everyday requests (no wake word), from the spoken test set: all held-out voices' are test.
    import json
    index = json.loads((HERE / "clips" / "index.json").read_text(encoding="utf-8"))
    import wave
    for item in index:
        if item["how"] == "fast" and rng.random() < 0.5:
            continue
        with wave.open(str(HERE / "clips" / item["file"])) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        out.append({"audio": audio, "wake": False, "voice": item["voice"], "test": item["voice"] in HELD_OUT,
                    "how": item["how"], "text": item["text"], "speech": True})
    # Noise alone: a fan, a room of people.
    for k in range(40):
        n = 16_000 * 3
        out.append({"audio": build.brown(n) * 300 + build.babble(n) * rng.uniform(0, 1), "wake": False, "voice": "noise",
                    "test": k % 4 == 0, "how": "noise", "text": "(noise)"})
    return out


def main() -> None:
    started = time.time()
    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    data = clips()
    print(f"{len(data)} clips made in {time.time() - started:.0f} s", flush=True)
    for item in data:
        item["scores"], item["feats"] = stream(item["audio"], model)
        del item["audio"]
    print(f"streamed in {time.time() - started:.0f} s", flush=True)

    # Training windows: for a wake clip, the steps around where the stock model peaks - or, where it
    # hears nothing (an accent it doesn't know), the steps just after the phrase ends; for the rest,
    # every other step.
    X, y = [], []
    for item in data:
        if item["test"]:
            continue
        feats = item["feats"].reshape(len(item["feats"]), -1)
        if item["wake"]:
            end = len(feats) - 4 - int(0.4 * 16_000 / FRAME)  # the phrase ends before the padding and the tail
            peak = int(np.argmax(item["scores"])) if item["scores"].max() > 0.3 else end
            for i in range(max(0, peak - 1), min(len(feats), peak + 3)):
                X.append(feats[i])
                y.append(1)
        else:
            for i in range(0, len(feats), 2):
                X.append(feats[i])
                y.append(0)
    X, y = np.array(X, dtype=np.float32), np.array(y)
    print(f"training on {len(y)} steps: {y.sum()} wake, {len(y) - y.sum()} not", flush=True)

    from sklearn.neural_network import MLPClassifier

    pos = np.where(y == 1)[0]
    idx = np.concatenate([np.arange(len(y)), np.repeat(pos, max(1, (len(y) - len(pos)) // len(pos) // 3))])
    net = MLPClassifier(hidden_layer_sizes=(96, 32), alpha=3e-3, max_iter=60, early_stopping=True, random_state=3,
                        batch_size=256, learning_rate_init=1e-3)
    net.fit(X[idx], y[idx])
    print(f"trained in {time.time() - started:.0f} s", flush=True)
    np.savez_compressed(sys.argv[1], **{f"w{i}": w.astype(np.float32) for i, w in enumerate(net.coefs_)},
                        **{f"b{i}": b.astype(np.float32) for i, b in enumerate(net.intercepts_)})

    # Held out: each clip's highest score, stock and new, as the app would see it.
    for item in data:
        feats = item["feats"].reshape(len(item["feats"]), -1).astype(np.float32)
        item["ours"] = float(net.predict_proba(feats)[:, 1].max())
        item["stock"] = float(item["scores"].max())
    test = [d for d in data if d["test"]]
    wake = [d for d in test if d["wake"]]
    near = [d for d in test if not d["wake"] and d.get("near")]
    speech = [d for d in test if not d["wake"] and not d.get("near")]
    print(f"\nheld out: {len(wake)} wake, {len(near)} lookalikes, {len(speech)} speech/noise")

    def rate(rows, rule):
        return 100 * sum(rule(d) for d in rows) / max(1, len(rows))

    rules = {"stock >= 0.5 (today)": lambda d: d["stock"] >= 0.5}
    for t in (0.5, 0.7, 0.8, 0.9, 0.95):
        rules[f"ours >= {t}"] = lambda d, t=t: d["ours"] >= t
        rules[f"stock>=0.5 & ours>=0.2, or ours>={t}"] = lambda d, t=t: (d["stock"] >= 0.5 and d["ours"] >= 0.2) or d["ours"] >= t
    for name, rule in rules.items():
        print(f"  {name:38s} wakes {rate(wake, rule):5.1f}%   lookalikes {rate(near, rule):5.1f}%   "
              f"speech {rate(speech, rule):4.1f}%")
    by_voice: dict = {}
    for d in wake:
        key = d["voice"] if not d["voice"].startswith("libritts") else "libritts (40 new speakers)"
        by_voice.setdefault(key, []).append(d)
    print("\nheld-out voices (today -> ours>=0.9 rule):")
    for v, rows in by_voice.items():
        print(f"  {v:28s} {rate(rows, rules['stock >= 0.5 (today)']):5.1f}% -> "
              f"{rate(rows, rules['stock>=0.5 & ours>=0.2, or ours>=0.9']):5.1f}%")


if __name__ == "__main__":
    main()
