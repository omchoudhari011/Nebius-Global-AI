"""How well "Hey Jarvis" is heard: said by every voice under every condition (it must wake), and
lookalikes and everyday speech (it mustn't) - streamed 80 ms at a time through the same openWakeWord
model the desktop app uses. Prints, for each threshold, how often it woke and how often by mistake."""
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import build  # noqa: E402  (its voices and conditions; importing builds nothing new)
from openwakeword.model import Model  # noqa: E402

HERE = Path(__file__).parent
WAKE = ["Hey Jarvis", "Hey Jarvis, open chrome", "Hey, Jarvis", "Jarvis, hey Jarvis"]
NEAR = ["Hey Travis", "Hey Marcus", "Hey there", "Hey Harvey", "Jars of jam", "Harvest season", "Hey service",
        "Hey Siri", "OK Google", "Hey Alexa", "Jarvis was the AI in Iron Man"]
FRAME = 1280


def score(audio: np.ndarray, model: Model) -> float:
    model.reset()
    audio = np.concatenate([np.zeros(FRAME * 4), audio, np.zeros(FRAME * 6)]).astype(np.int16)
    best = 0.0
    for i in range(0, len(audio) - FRAME, FRAME):
        best = max(best, max(model.predict(audio[i:i + FRAME]).values()))
    return best


def main() -> None:
    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    voices = build.VOICES_EN
    rows = []
    for text in WAKE + NEAR:
        for name in voices:
            for how in ("clean", "fast", "noisy", "far"):
                rows.append((text, name, how, text in WAKE, score(build.condition(name, text, how), model)))
    # Everyday speech: the command recordings themselves (no wake word in them).
    clips = HERE / "clips"
    for path in sorted(clips.glob("en*-clean.wav"))[:120] + sorted(clips.glob("en*-noisy.wav"))[:120]:
        with wave.open(str(path)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        rows.append((path.name, "", "speech", False, score(audio, model)))
    np.save(HERE / "wake-scores.npy", np.array([(r[0], r[1], r[2], r[3], r[4]) for r in rows], dtype=object), allow_pickle=True)

    wake = [r for r in rows if r[3]]
    near = [r for r in rows if not r[3] and r[2] != "speech"]
    speech = [r for r in rows if r[2] == "speech"]
    print(f"{len(wake)} with the wake word, {len(near)} lookalikes, {len(speech)} everyday requests")
    for t in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        woke = sum(r[4] >= t for r in wake) / len(wake)
        false_near = sum(r[4] >= t for r in near) / len(near)
        false_speech = sum(r[4] >= t for r in speech) / len(speech)
        print(f"  threshold {t:.1f}: wakes {100 * woke:5.1f}%   lookalikes wake it {100 * false_near:5.1f}%   "
              f"everyday speech {100 * false_speech:4.1f}%")
    print("\nmissed at 0.5, by condition and voice:")
    for key, at in (("how", 2), ("voice", 1)):
        groups: dict = {}
        for r in wake:
            groups.setdefault(r[at], []).append(r[4] >= 0.5)
        print(f"  {key}: " + ", ".join(f"{k} {100 * sum(v) / len(v):.0f}%" for k, v in sorted(groups.items())))
    print("\nlookalikes that woke it at 0.5:", sorted({r[0] for r in near if r[4] >= 0.5}))


if __name__ == "__main__":
    main()
