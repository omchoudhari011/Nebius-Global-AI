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
    wake_word: bool = True  # listen for "Hey Jarvis"
    wake_threshold: float = 0.5  # openWakeWord score that counts as the wake word
    hotkey: str = "<ctrl>+<alt>+<space>"
    speak_replies: bool = True
    speak_announcements: bool = True  # reminders, meeting heads-ups, alerts and the morning briefing, out loud
    voice: str = "en_US-lessac-medium"  # Piper voice in APP_DIR/voices; falls back to Windows' own voice
    microphone: int | None = None  # sounddevice input index; None = the default microphone
    protected: list[str] = field(default_factory=lambda: [
        r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData",
    ])  # folders the PC actions may never change, whatever is confirmed

    @classmethod
    def load(cls) -> Settings:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


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
