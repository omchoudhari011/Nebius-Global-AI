"""Compare ways of hearing on the same recordings: which settings get the most requests right.
Usage (from voice-lab): compare.py <clips> [variant ...]   variants: A B C D E (see VARIANTS)."""
import io
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("WHISPER_URL", "local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import voice  # noqa: E402
from faster_whisper import decode_audio  # noqa: E402

from route import correct, route  # noqa: E402

NAMES = ["Tanishka", "Meera", "Priya", "Pune", "Infosys"]
VARIANTS = {
    "A": "any language, no hint (as it was)",
    "B": "English, Hindi or Marathi; no hint",
    "C": "the three languages + the command hint",
    "D": "the three languages + the hint + the user's names",
    "E": "C, and when English and Hindi are close, both - keeping the one that's a known command",
    "F": "B, and when English and Hindi are close, both - keeping the one that's a known command",
}


def hear(model, samples, variant: str) -> str:
    def run(language, prompt):
        segments, _ = model.transcribe(samples, language=language, beam_size=5, vad_filter=True, initial_prompt=prompt,
                                       condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        return "" if prompt and voice.echoed(text, prompt) else text

    if variant == "A":
        return run(None, None)
    _l, _p, ranked = model.detect_language(samples, vad_filter=True)
    score = {c: 0.0 for c in voice.SPOKEN}
    for code, p in ranked:
        code = voice.SAME_AS.get(code, code)
        if code in score:
            score[code] += p
    order = sorted(score, key=score.get, reverse=True)
    language = order[0]
    if variant == "B":
        return run(language, None)
    if variant == "F":
        text = run(language, None)
        if score[order[1]] >= 0.25 * score[order[0]] and route(text)[0] == "skill":
            other = run(order[1], None)
            if route(other)[0] != "skill":
                return other
        return text
    if variant in ("C", "E"):
        text = run(language, voice.hint(language))
        if variant == "E" and score[order[1]] >= 0.25 * score[order[0]] and route(text)[0] == "skill":
            other = run(order[1], voice.hint(order[1]))
            if route(other)[0] != "skill":
                return other
        return text
    return run(language, voice.hint(language, NAMES))


def main() -> None:
    clips = Path(sys.argv[1])
    variants = sys.argv[2:] or list(VARIANTS)
    index = json.loads((clips / "index.json").read_text(encoding="utf-8"))
    keep = {"en_GB-alan-medium", "en_US-amy-medium", "hi_IN-rohan-medium", "hi_IN-priyamvada-medium", "hi_IN-pratham-medium"}
    subset = [i for i in index if i["voice"] in keep and i["how"] in ("clean", "noisy")]
    model = voice.LOCAL._load()
    audio = {i["file"]: decode_audio(io.BytesIO((clips / i["file"]).read_bytes())) for i in subset}
    for variant in variants:
        started, rows = time.time(), []
        for item in subset:
            heard = hear(model, audio[item["file"]], variant)
            want = tuple(item["want"][:2]) + ((item["want"][2],) if len(item["want"]) > 2 else ())
            rows.append({**item, "heard": heard, "ok": correct(route(heard), want)})
        groups = defaultdict(list)
        for r in rows:
            groups["clean" if r["how"] == "clean" else "noisy"].append(r["ok"])
            groups["Indian voices" if r["voice"].startswith("hi_") else "other voices"].append(r["ok"])
            groups["Hindi requests" if r["lang"] == "hi" else "English requests"].append(r["ok"])
        ok = sum(r["ok"] for r in rows)
        print(f"{variant}: {100 * ok / len(rows):5.1f}%  ({VARIANTS[variant]}; {time.time() - started:.0f} s)  " +
              ", ".join(f"{k} {100 * sum(v) / len(v):.0f}%" for k, v in sorted(groups.items())), flush=True)
        Path(f"compare-{variant}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main()
