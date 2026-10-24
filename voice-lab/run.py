"""Hear every recording as Jarvis does - the server's own speech to text (WHISPER_URL=local) - then
route what was heard, and score it: was the right thing done? Usage: run.py <clips> <results.json> [language]"""
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("WHISPER_URL", "local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import voice  # noqa: E402

from route import correct, route  # noqa: E402

clips = Path(sys.argv[1])
language = sys.argv[3] if len(sys.argv) > 3 else None
index = json.loads((clips / "index.json").read_text(encoding="utf-8"))
results, started = [], time.time()
for item in index:
    audio = (clips / item["file"]).read_bytes()
    t = time.time()
    try:
        heard = voice.transcribe(audio, "audio/wav", language)
    except voice.VoiceError as exc:
        heard = f"<{exc}>"
    took = time.time() - t
    got = route(heard)
    want = tuple(item["want"][:2]) + ((item["want"][2],) if len(item["want"]) > 2 else ())
    results.append({**item, "heard": heard, "got": got, "ok": correct(got, want), "seconds": round(took, 2)})
Path(sys.argv[2]).write_text(json.dumps(results, ensure_ascii=False, indent=0), encoding="utf-8")

ok = sum(r["ok"] for r in results)
print(f"{ok}/{len(results)} right ({100 * ok / len(results):.1f}%), {time.time() - started:.0f} s, "
      f"median {sorted(r['seconds'] for r in results)[len(results) // 2]} s per recording")
for key in ("how", "voice", "lang"):
    groups = defaultdict(list)
    for r in results:
        groups[r[key]].append(r["ok"])
    print(f"  by {key}: " + ", ".join(f"{k} {100 * sum(v) / len(v):.0f}%" for k, v in sorted(groups.items())))
by_text = defaultdict(list)
for r in results:
    by_text[r["text"]].append(r)
print("\nworst requests:")
for text, rs in sorted(by_text.items(), key=lambda kv: sum(r["ok"] for r in kv[1]) / len(kv[1]))[:15]:
    rate = sum(r["ok"] for r in rs) / len(rs)
    if rate == 1:
        break
    wrong = [r for r in rs if not r["ok"]]
    heard = "; ".join(sorted({r["heard"][:60] for r in wrong})[:4])
    print(f"  {100 * rate:3.0f}%  {text}\n        heard: {heard}")
