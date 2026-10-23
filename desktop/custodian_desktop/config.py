"""Settings in %APPDATA%\\Custodian\\desktop.json; the session token in Windows Credential
Manager (keyring), never in a file."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import keyring

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Custodian"
SETTINGS_FILE = APP_DIR / "desktop.json"
KEYRING_SERVICE = "Custodian desktop"


@dataclass
class Settings:
    server: str = "http://localhost:8001"
    wake_word: bool = True  # listen for the wake word
    wake_threshold: float = 0.5  # openWakeWord score that counts as the wake word
    # openWakeWord's "hey_jarvis", or your own model's name in APP_DIR/wakewords ("hey_custodian" for
    # hey_custodian.onnx); wake_phrase is what the app tells you to say.
    wake_model: str = "hey_jarvis"
    wake_phrase: str = "Hey Jarvis"
    barge_in: bool = True  # saying the wake word while Custodian speaks cuts it off
    fast_transcribe: bool = True  # start transcribing at the first pause in what you say
    hotkey: str = "<ctrl>+<alt>+<space>"
    speak_replies: bool = True
    speak_announcements: bool = True  # reminders, meeting heads-ups, alerts and the morning briefing, out loud
    voice: str = "en_GB-alan-medium"  # Piper voice in APP_DIR/voices (Jarvis: British); else lessac, else Windows' own
    hindi_voice: str = "hi_IN-pratham-medium"  # for replies in Hindi or Marathi (Devanagari)
    voice_id: bool = False  # only take spoken requests from the voice set up with "Set up voice ID"
    voice_id_threshold: float = 0.45
    microphone: int | None = None  # sounddevice input index; None = the default microphone
    # Jarvis
    conversation_mode: bool = True  # after a reply, listen a few seconds for a follow-up without the wake word
    follow_up_seconds: float = 6.0
    hud: bool = True  # the J.A.R.V.I.S. HUD window (needs pywebview); off = the small text panel
    hud_hotkey: str = "<ctrl>+<alt>+j"  # the full-screen HUD
    watchdog: bool = True  # speak up about a low battery, an overloaded processor or memory, a full drive
    greet: bool = True  # say hello when the app starts
    settings_version: int = 2
    protected: list[str] = field(default_factory=lambda: [
        r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData",
    ])  # folders the PC actions may never change, whatever is confirmed

    @classmethod
    def load(cls) -> Settings:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if data.get("settings_version", 1) < 2 and data.get("voice") == "en_US-lessac-medium":
                data["voice"] = "en_GB-alan-medium"  # version 2: Jarvis's British voice, unless you'd chosen another
            data["settings_version"] = 2
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def wake_model_path(name: str) -> str:
    """Your own wake word's model file in APP_DIR/wakewords when there is one, else the name."""
    for suffix in (".onnx", ".tflite"):
        path = APP_DIR / "wakewords" / f"{name}{suffix}"
        if path.exists():
            return str(path)
    return name


def voice_name(wanted: str) -> str:
    """`wanted` if its Piper voice is installed, else the first installed of the usual ones."""
    for name in (wanted, "en_GB-alan-medium", "en_US-lessac-medium"):
        if (APP_DIR / "voices" / f"{name}.onnx").exists():
            return name
    return wanted


def get_token(server: str) -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, server)
    except keyring.errors.KeyringError:
        return None


def set_token(server: str, token: str | None) -> None:
    try:
        if token:
            keyring.set_password(KEYRING_SERVICE, server, token)
        else:
            keyring.delete_password(KEYRING_SERVICE, server)
    except keyring.errors.KeyringError:
        pass
