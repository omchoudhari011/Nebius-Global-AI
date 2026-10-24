"""Encrypted backups of everything: made safely while running, kept to the newest few, unreadable
without APP_SECRET_KEY, and restored without losing what was there."""

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app import backup, cli, crypto, db, memory


def test_a_backup_is_one_encrypted_file_and_the_oldest_go(user_id, monkeypatch, tmp_path):
    memory.write(user_id, "My locker code is near the gym entrance.", "semantic")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("BACKUP_KEEP", "2")
    made = [backup.make() for _ in range(3)]
    names = [b["name"] for b in backup.listing()]
    assert len(names) == 2 and made[0]["file"].endswith(".cbk") and made[0]["removed"] == 0 and made[2]["removed"] == 1
    sealed = (tmp_path / "backups" / names[0]).read_bytes()
    assert b"SQLite format" not in sealed and b"custodian.db" not in sealed and b"locker" not in sealed
    status = backup.status()
    assert status["count"] == 2 and status["last"]["name"] == names[0] and status["time"] == "03:30"


def test_a_backup_comes_back_and_what_was_there_is_kept_aside(user_id, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    kept = memory.write(user_id, "I prefer aisle seats.", "preference")
    made = backup.make()
    with db.connect() as conn:
        conn.execute("DELETE FROM memories")
    done = backup.restore(Path(made["file"]))
    assert "custodian.db" in done["restored"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = ?", (kept,)).fetchone()[0] == 1
    old = sqlite3.connect(Path(done["previous_data"]) / "custodian.db")
    try:
        assert old.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0  # the emptied one, kept, not deleted
    finally:
        old.close()


def test_another_key_cant_open_it(user_id, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    made = backup.make()
    monkeypatch.setattr(crypto, "_fernet", lambda: crypto._fernet_for((Fernet.generate_key().decode(),)))
    with pytest.raises(backup.BackupError, match="can't be opened with this APP_SECRET_KEY"):
        backup.restore(Path(made["file"]))
    with pytest.raises(backup.BackupError, match="no backup"):
        backup.restore(tmp_path / "missing.cbk")


def test_back_up_now_from_the_app_and_the_cli(user_id, client, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "b"))
    assert client.get("/privacy/backups").json()["count"] == 0
    after = client.post("/privacy/backups").json()
    assert after["count"] == 1 and after["folder"] == str(tmp_path / "b")
    assert cli.main(["backup", str(tmp_path / "c")]) == 0
    assert "encrypted" in capsys.readouterr().out and len(list((tmp_path / "c").glob("*.cbk"))) == 1
    monkeypatch.setenv("BACKUP_TIME", "off")
    services = {s["purpose"]: s for s in client.get("/privacy").json()["other_services"]}
    assert backup.at() == "" and "only when you make one" in services["Encrypted backups"]["sends"]
