"""Make the spoken test set: every request in suite.py, in several voices, under four conditions -
normal, fast (in a hurry), noisy (someone talking nearby and a fan), far (across the room, through a
laptop's microphone). 16 kHz mono WAVs, as the desktop app records. Usage: build.py <out folder>"""
import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
from piper import PiperVoice, SynthesisConfig

from suite import EN, HI, VOICES_EN, VOICES_HI  # noqa: F401

HERE = Path(__file__).parent
RATE = 16_000
rng = np.random.default_rng(7)
voices: dict[str, PiperVoice] = {}


def voice(name: str) -> PiperVoice:
    if name not in voices:
        voices[name] = PiperVoice.load(str(HERE / "voices" / f"{name}.onnx"))
    return voices[name]


def speak(name: str, text: str, speed: float = 1.0) -> np.ndarray:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        voice(name).synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1 / speed))
    buf.seek(0)
    with wave.open(buf) as w:
        rate, audio = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if rate != RATE:  # to the microphone's 16 kHz
        audio = np.interp(np.arange(0, len(audio), rate / RATE), np.arange(len(audio)), audio)
    return audio


def pad(audio: np.ndarray) -> np.ndarray:
    """A little room before and after, as a recording has."""
    return np.concatenate([np.zeros(int(0.25 * RATE)), audio, np.zeros(int(0.4 * RATE))])


def at_snr(signal: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    noise = np.resize(noise, len(signal))
    power = np.mean(signal**2) / (np.mean(noise**2) + 1e-9) / (10 ** (snr_db / 10))
    return signal + noise * np.sqrt(power)


def brown(n: int) -> np.ndarray:  # a fan, an AC: low rumble
    x = np.cumsum(rng.normal(size=n))
    x -= np.convolve(x, np.ones(400) / 400, mode="same")
    return x


BABBLE = None


def babble(n: int) -> np.ndarray:  # someone else talking in the room
    global BABBLE
    if BABBLE is None:
        BABBLE = np.concatenate([speak("en_US-ryan-medium", "So I told him we should probably leave by nine, "
                                       "because the traffic near the station gets really bad after that."),
                                 speak("en_GB-jenny_dioco-medium", "Did you see the match last night? It went on for ages.")])
    return np.resize(BABBLE, n)


def laptop_mic(audio: np.ndarray) -> np.ndarray:
    """300-3400 Hz, as a cheap built-in microphone hears, a small room's echo, and quiet."""
    spectrum = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(len(audio), 1 / RATE)
    spectrum[(freqs < 300) | (freqs > 3400)] *= 0.05
    audio = np.fft.irfft(spectrum, len(audio))
    echo = np.zeros(int(0.25 * RATE))
    echo[0] = 1.0
    taps = rng.choice(len(echo), 40, replace=False)
    echo[taps] = rng.uniform(0.05, 0.3, 40) * np.exp(-taps / (0.08 * RATE))
    return np.convolve(audio, echo)[: len(audio)] * 0.25


def condition(name: str, text: str, how: str) -> np.ndarray:
    if how == "fast":
        return pad(speak(name, text, 1.3))
    audio = pad(speak(name, text))
    if how == "noisy":
        audio = at_snr(audio, 0.6 * babble(len(audio)) + brown(len(audio)), 8)
    elif how == "far":
        audio = at_snr(laptop_mic(audio), brown(len(audio)), 15)
    return audio


def save(path: Path, audio: np.ndarray) -> None:
    audio = np.clip(audio, -32000, 32000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())


if __name__ == "__main__":
    OUT = Path(sys.argv[1]).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for group, items, names in (("en", EN, VOICES_EN), ("hi", HI, VOICES_HI)):
        for n, (text, want) in enumerate(items):
            for name in names:
                for how in ("clean", "fast", "noisy", "far"):
                    path = OUT / f"{group}{n:02d}-{name}-{how}.wav"
                    if not path.exists():
                        save(path, condition(name, text, how))
                    index.append({"file": path.name, "text": text, "want": want, "voice": name, "how": how, "lang": group})
        print(group, "done", flush=True)
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(index), "recordings")
