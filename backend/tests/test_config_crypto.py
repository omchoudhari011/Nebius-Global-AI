import json

import pytest
from cryptography.fernet import Fernet

from app import crypto
from app.config import ConfigError, load_settings, reset_settings


def test_production_requires_secrets(monkeypatch):
    monkeypatch.setenv("CUSTODIAN_ENV", "production")
    monkeypatch.delenv("APP_SECRET_KEY")
    with pytest.raises(ConfigError, match="APP_SECRET_KEY"):
        load_settings()


def test_production_rejects_weak_passcode(monkeypatch):
    monkeypatch.setenv("CUSTODIAN_ENV", "production")
    monkeypatch.setenv("CUSTODIAN_PASSCODE", "change-me")
    with pytest.raises(ConfigError, match="CUSTODIAN_PASSCODE"):
        load_settings()


def test_development_generates_and_keeps_secrets(monkeypatch, tmp_path):
    for name in ("APP_SECRET_KEY", "SESSION_SECRET", "CUSTODIAN_PASSCODE"):
        monkeypatch.delenv(name)
    first = load_settings()
    second = load_settings()
    assert first.passcode_generated
    assert first.passcode == second.passcode
    assert first.app_secret_keys == second.app_secret_keys
    stored = json.loads((first.data_dir / ".dev-secrets.json").read_text())
    assert stored["CUSTODIAN_PASSCODE"] == first.passcode


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CUSTODIAN_TIMEZONE", "Mars/Olympus", "IANA"),
        ("MODEL_BACKEND", "gpt", "MODEL_BACKEND"),
        ("CORS_ORIGINS", "*", "CORS_ORIGINS"),
        ("APP_SECRET_KEY", "not-a-key", "Fernet"),
        ("MODEL_EXTRA_BODY", "[1]", "JSON object"),
        ("TOOL_TIMEOUT_SECONDS", "0", "TOOL_TIMEOUT_SECONDS"),
    ],
)
def test_invalid_settings_are_rejected(monkeypatch, name, value, message):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=message):
        load_settings()


def test_nebius_needs_an_api_key(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "nebius")
    with pytest.raises(ConfigError, match="NEBIUS_API_KEY"):
        load_settings()


def test_samesite_none_needs_secure_cookies(monkeypatch):
    monkeypatch.setenv("COOKIE_SAMESITE", "none")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(ConfigError, match="COOKIE_SECURE"):
        load_settings()


def test_encrypt_round_trip_is_not_plaintext():
    token = crypto.encrypt("dentist on Friday")
    assert token.startswith(b"gAAAAA")
    assert b"dentist" not in token
    assert crypto.decrypt(token) == "dentist on Friday"
    assert crypto.decrypt_json(crypto.encrypt_json({"a": [1, "é"]})) == {"a": [1, "é"]}


def test_key_rotation(monkeypatch):
    old = crypto.encrypt("kept across rotation")
    new_key = Fernet.generate_key().decode()
    old_key = load_settings().app_secret_keys[0]

    monkeypatch.setenv("APP_SECRET_KEY", f"{new_key},{old_key}")
    reset_settings()
    assert crypto.decrypt(old) == "kept across rotation"
    rotated = crypto.rotate(old)

    monkeypatch.setenv("APP_SECRET_KEY", new_key)
    reset_settings()
    assert crypto.decrypt(rotated) == "kept across rotation"
    with pytest.raises(crypto.InvalidToken):
        crypto.decrypt(old)
