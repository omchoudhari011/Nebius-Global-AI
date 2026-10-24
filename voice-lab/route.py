"""What Jarvis does with what it heard - the same order as the real thing: the PC's own commands
first (that's all, the HUD), then the server's exact commands (fast path), then the model's skill."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import fastpath, skills  # noqa: E402

spec = importlib.util.spec_from_file_location("jarvis", ROOT / "desktop" / "custodian_desktop" / "jarvis.py")
jarvis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jarvis)


def route(text: str) -> tuple:
    if jarvis.ends_conversation(text):
        return ("end",)
    command = jarvis.hud_command(text)
    if command:
        return ("hud", command)
    intent = fastpath.match(text)
    if intent is not None:
        return ("fast", intent.function, intent.args)
    return ("skill", skills.pick_skill_name(text))


def correct(got: tuple, want: tuple) -> bool:
    if want[0] != got[0]:
        return False
    if want[0] == "fast":
        if got[1] != want[1]:
            return False
        for key, value in want[2].items():
            have = got[2].get(key)
            if isinstance(value, str):
                if not isinstance(have, str) or value.lower() not in have.lower():
                    return False
            elif have != value:
                return False
        return True
    return got[1:] == want[1:]


if __name__ == "__main__":
    from suite import EN, HI

    bad = 0
    for text, want in EN + HI:
        got = route(text)
        ok = correct(got, want)
        bad += not ok
        if not ok:
            print(f"MISS  {text!r}\n      want {want}\n      got  {got}")
    print(f"{len(EN + HI) - bad}/{len(EN + HI)} routed right from the exact words")
