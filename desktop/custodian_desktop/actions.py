"""The PC actions the server's model can ask for. Each takes the validated arguments and
returns a JSON-able result, or raises with a message the model reads.

Safety lives here as well as on the server: anything marked `confirm` was already approved
by you in a dialog before it gets here; files inside the protected folders (Windows, Program
Files, ...) are never changed; deleting goes to the Recycle Bin; nothing is overwritten
unless the request says so.
"""

from __future__ import annotations

import base64
import ctypes
import datetime as dt
import fnmatch
import glob
import io
import os
import re
import shutil
import time
import webbrowser
from pathlib import Path

MAX_READ = 8000
MAX_LIST = 100
SKIP_DIRS = {"AppData", "node_modules", ".git", "$Recycle.Bin", "Windows", ".venv", "venv", "__pycache__", "site-packages",
             "anaconda3", "miniconda3", "envs", ".cache", ".conda", "Program Files", "Program Files (x86)", "ProgramData"}

# Common names people say, mapped to what Windows can start.
APPS = {
    "notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint", "explorer": "explorer",
    "file explorer": "explorer", "files": "explorer", "task manager": "taskmgr", "settings": "ms-settings:",
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge", "firefox": "firefox",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "vs code": "code",
    "vscode": "code", "visual studio code": "code", "spotify": "spotify:", "camera": "microsoft.windows.camera:",
    "clock": "ms-clock:", "mail": "outlookmail:", "store": "ms-windows-store:", "terminal": "wt", "cmd": "cmd",
}
FILE_EXTENSIONS = {"pdf", "txt", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg", "gif", "mp3", "mp4",
                   "wav", "zip", "exe", "msi", "md", "py", "js", "ts", "csv", "json", "html", "htm", "log", "lnk", "bat", "ps1"}
VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "mute": 0xAD, "volume_up": 0xAF, "volume_down": 0xAE}


class ActionError(RuntimeError):
    pass


def _path(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))).resolve()


def _guard(path: Path, protected: list[str]) -> None:
    for root in protected:
        root_path = Path(root).resolve()
        if path == root_path or root_path in path.parents:
            raise ActionError(f"{path} is inside {root_path}, which Custodian never changes")


def _key(vk: int, times: int = 1) -> None:
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
        time.sleep(0.02)


# Looking ---------------------------------------------------------------------------------------

def system_info(**_: object) -> dict:
    import psutil

    battery = psutil.sensors_battery()
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            use = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({"drive": part.device, "free_gb": round(use.free / 1e9, 1), "total_gb": round(use.total / 1e9, 1)})
    memory = psutil.virtual_memory()
    return {
        "time": dt.datetime.now().strftime("%A %d %B %Y, %H:%M"),
        "battery": {"percent": round(battery.percent), "charging": battery.power_plugged} if battery else None,
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory": {"used_percent": memory.percent, "total_gb": round(memory.total / 1e9, 1)},
        "disks": disks,
        "active_window": _active_title(),
    }


def _active_title() -> str | None:
    try:
        import pygetwindow

        window = pygetwindow.getActiveWindow()
        return window.title if window else None
    except Exception:  # noqa: BLE001
        return None


def list_files(path: str = "~", pattern: str = "*", **_: object) -> dict:
    folder = _path(path)
    if not folder.is_dir():
        raise ActionError(f"{folder} isn't a folder")
    items = []
    for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        items.append({"name": entry.name, "type": "folder" if entry.is_dir() else "file",
                      "size_kb": None if entry.is_dir() else round(stat.st_size / 1024, 1),
                      "modified": dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")})
        if len(items) >= MAX_LIST:
            break
    return {"folder": str(folder), "items": items, "more": len(items) >= MAX_LIST}


def find_files(query: str, folder: str = "~", **_: object) -> dict:
    root, needle, found = _path(folder), query.lower(), []
    deadline = time.monotonic() + 10
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files + dirs:
            if needle in name.lower():
                found.append(str(Path(current) / name))
                if len(found) >= 30:
                    return {"query": query, "matches": found, "more": True}
        if time.monotonic() > deadline:
            return {"query": query, "matches": found, "more": True, "note": "stopped after 10 seconds"}
    return {"query": query, "matches": found, "more": False}


def read_file(path: str, **_: object) -> dict:
    file = _path(path)
    if not file.is_file():
        raise ActionError(f"{file} isn't a file")
    data = file.read_bytes()[: MAX_READ * 4]
    if b"\x00" in data[:2048]:
        raise ActionError(f"{file.name} isn't a text file")
    text = data.decode("utf-8", "replace")
    return {"path": str(file), "text": text[:MAX_READ] + (" …(cut)" if len(text) > MAX_READ else "")}


def list_windows(**_: object) -> dict:
    import pygetwindow

    return {"windows": [t for t in pygetwindow.getAllTitles() if t.strip()][:60], "active": _active_title()}


def get_clipboard(**_: object) -> dict:
    import pyperclip

    text = pyperclip.paste() or ""
    return {"text": text[:MAX_READ]}


def screenshot(**_: object) -> dict:
    import pyautogui

    image = pyautogui.screenshot()
    folder = Path.home() / "Pictures" / "Custodian"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot-{dt.datetime.now():%Y%m%d-%H%M%S}.png"
    image.save(path)
    small = image.copy()
    small.thumbnail((1280, 1280))
    buffer = io.BytesIO()
    small.save(buffer, format="PNG")
    return {"path": str(path), "image_b64": base64.b64encode(buffer.getvalue()).decode()}


# Doing -----------------------------------------------------------------------------------------

def _start_menu_shortcut(name: str) -> Path | None:
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")), Path(os.environ.get("APPDATA", ""))]
    best = None
    for root in roots:
        for link in glob.glob(str(root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "**" / "*.lnk"), recursive=True):
            stem = Path(link).stem.lower()
            if stem == name:
                return Path(link)
            if name in stem and (best is None or len(stem) < len(best.stem)):
                best = Path(link)
    return best


def open(target: str, **_: object) -> dict:  # noqa: A001 - the action's name
    target = target.strip()
    domain = re.fullmatch(r"[\w-]+(?:\.[\w-]+)*\.([a-z]{2,})(?:/\S*)?", target.lower())
    bare_domain = bool(domain) and domain.group(1) not in FILE_EXTENSIONS and not _path(target).exists()
    if target.lower().startswith(("http://", "https://", "www.")) or bare_domain:
        url = target if "://" in target else "https://" + target
        webbrowser.open(url)
        return {"opened": url, "kind": "web page"}
    path = _path(target)
    if path.exists():
        os.startfile(str(path))  # noqa: S606 - the user asked to open it
        return {"opened": str(path), "kind": "folder" if path.is_dir() else "file"}
    name = target.lower().removesuffix(".exe")
    command = APPS.get(name)
    if command is None:
        shortcut = _start_menu_shortcut(name)
        if shortcut is None:
            raise ActionError(f"couldn't find an app called {target!r}")
        os.startfile(str(shortcut))  # noqa: S606
        return {"opened": shortcut.stem, "kind": "app"}
    try:
        os.startfile(command)  # noqa: S606 - a fixed app name or protocol from APPS
    except OSError:
        shortcut = _start_menu_shortcut(name)
        if shortcut is None:
            raise ActionError(f"{target} doesn't seem to be installed") from None
        os.startfile(str(shortcut))  # noqa: S606
    return {"opened": target, "kind": "app"}


def media(action: str, steps: int = 1, **_: object) -> dict:
    _key(VK[action], steps if action in ("volume_up", "volume_down") else 1)
    return {"done": action, "steps": steps}


def set_volume(level: int, **_: object) -> dict:
    _key(VK["volume_down"], 50)  # to zero (each key press is 2%)
    _key(VK["volume_up"], round(level / 2))
    return {"volume": round(level / 2) * 2}


def lock_screen(**_: object) -> dict:
    ctypes.windll.user32.LockWorkStation()
    return {"locked": True}


def _window(title: str):  # noqa: ANN202
    import pygetwindow

    matches = [w for w in pygetwindow.getAllWindows() if title.lower() in w.title.lower() and w.title.strip()]
    if not matches:
        raise ActionError(f"no open window has {title!r} in its title")
    return matches[0]


def focus_window(title: str, **_: object) -> dict:
    window = _window(title)
    if window.isMinimized:
        window.restore()
    window.activate()
    return {"focused": window.title}


def close_window(title: str, **_: object) -> dict:
    window = _window(title)
    window.close()
    return {"closed": window.title}


def type_text(text: str, **_: object) -> dict:
    import pyautogui
    import pyperclip

    if text.isascii():
        pyautogui.write(text, interval=0.01)
    else:  # pyautogui types ASCII only: paste anything else, then put the clipboard back
        before = pyperclip.paste()
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)
        pyperclip.copy(before)
    return {"typed_characters": len(text)}


KEY_NAMES = {"win": "winleft", "windows": "winleft", "control": "ctrl", "escape": "esc", "return": "enter", "del": "delete"}


def press_keys(keys: str, **_: object) -> dict:
    import pyautogui

    parts = [KEY_NAMES.get(k.strip().lower(), k.strip().lower()) for k in keys.replace(" ", "").split("+") if k.strip()]
    invalid = [k for k in parts if k not in pyautogui.KEYBOARD_KEYS]
    if invalid:
        raise ActionError(f"unknown key(s): {', '.join(invalid)}")
    pyautogui.hotkey(*parts)
    return {"pressed": "+".join(parts)}


def click(x: int, y: int, button: str = "left", **_: object) -> dict:
    import pyautogui

    width, height = pyautogui.size()
    if x >= width or y >= height:
        raise ActionError(f"({x}, {y}) is off the screen ({width}x{height})")
    if button == "double":
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.click(x, y, button=button)
    return {"clicked": [x, y], "button": button}


def write_file(path: str, content: str, overwrite: bool = False, *, protected: list[str], **_: object) -> dict:
    file = _path(path)
    _guard(file, protected)
    if file.exists() and not overwrite:
        raise ActionError(f"{file} already exists; ask to overwrite it")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    return {"written": str(file), "characters": len(content)}


def move_file(source: str, destination: str, *, protected: list[str], **_: object) -> dict:
    src, dest = _path(source), _path(destination)
    _guard(src, protected)
    _guard(dest, protected)
    if not src.exists():
        raise ActionError(f"{src} doesn't exist")
    if dest.is_dir():
        dest = dest / src.name
    if dest.exists():
        raise ActionError(f"{dest} already exists")
    shutil.move(str(src), str(dest))
    return {"moved": str(src), "to": str(dest)}


def delete_file(path: str, *, protected: list[str], **_: object) -> dict:
    from send2trash import send2trash

    target = _path(path)
    _guard(target, protected)
    if target == Path.home() or len(target.parts) <= 2:
        raise ActionError(f"Custodian won't delete {target}")
    if not target.exists():
        raise ActionError(f"{target} doesn't exist")
    send2trash(str(target))
    return {"moved_to_recycle_bin": str(target)}


def set_clipboard(text: str, **_: object) -> dict:
    import pyperclip

    pyperclip.copy(text)
    return {"copied_characters": len(text)}


ACTIONS = {
    "system_info": system_info, "list_files": list_files, "find_files": find_files, "read_file": read_file,
    "list_windows": list_windows, "get_clipboard": get_clipboard, "screenshot": screenshot, "open": open,
    "media": media, "set_volume": set_volume, "lock_screen": lock_screen, "focus_window": focus_window,
    "close_window": close_window, "type_text": type_text, "press_keys": press_keys, "click": click,
    "write_file": write_file, "move_file": move_file, "delete_file": delete_file, "set_clipboard": set_clipboard,
}
NEEDS_PROTECTED = {"write_file", "move_file", "delete_file"}


def run(function: str, args: dict, protected: list[str]) -> object:
    action = ACTIONS.get(function)
    if action is None:
        raise ActionError(f"this desktop app doesn't know how to {function}; update it")
    extra = {"protected": protected} if function in NEEDS_PROTECTED else {}
    return action(**args, **extra)


def describe(function: str, args: dict) -> str:
    """What the confirmation dialog shows: the action in plain words, with its exact details."""
    shown = {
        "type_text": lambda a: f"Type this into the window in front:\n\n{a['text'][:500]}",
        "press_keys": lambda a: f"Press {a['keys']}",
        "click": lambda a: f"Click ({a.get('button', 'left')}) at x={a['x']}, y={a['y']}",
        "write_file": lambda a: f"{'Overwrite' if a.get('overwrite') else 'Create'} the file\n{a['path']}\n\n{a['content'][:400]}",
        "move_file": lambda a: f"Move\n{a['source']}\nto\n{a['destination']}",
        "delete_file": lambda a: f"Move to the Recycle Bin:\n{a['path']}",
        "close_window": lambda a: f"Close the window \u201c{a['title']}\u201d (unsaved work may be lost)",
        "read_file": lambda a: f"Read the file\n{a['path']}\nand send its text to Custodian",
        "get_clipboard": lambda a: "Send the text on your clipboard to Custodian",
        "screenshot": lambda a: "Take a screenshot and send it to Custodian to describe",
    }
    return shown.get(function, lambda a: f"{function}: {a}")(args)
