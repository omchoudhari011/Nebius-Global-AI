import sqlite3

from app import cli, crypto, db, memory
from app.config import load_settings, reset_settings


def test_init_env_writes_valid_production_settings(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".env"
    assert cli.main(["init-env", "--production", "--timezone", "Asia/Kolkata", "--path", str(target)]) == 0
    assert "Sign-in passcode:" in capsys.readouterr().out
    assert cli.main(["init-env", "--path", str(target)]) == 1  # refuses to overwrite

    values = dict(
        line.split("=", 1) for line in target.read_text().splitlines() if line and not line.startswith("#")
    )
    for name in ("APP_SECRET_KEY", "SESSION_SECRET", "CUSTODIAN_PASSCODE", "CUSTODIAN_TIMEZONE"):
        monkeypatch.setenv(name, values[name])
    monkeypatch.setenv("CUSTODIAN_ENV", "production")
    settings = load_settings()  # production validation passes
    assert settings.timezone == "Asia/Kolkata"


def test_seed_check_and_backup(user_id, tmp_path, capsys):
    assert cli.main(["seed-demo"]) == 0
    assert cli.main(["check"]) == 0
    assert '"integrity": "ok"' in capsys.readouterr().out

    assert cli.main(["backup", str(tmp_path / "backups")]) == 0
    [file] = (tmp_path / "backups").iterdir()  # one encrypted file with every database
    from app import backup

    archive = backup.open_backup(file)
    assert {"custodian.db", "tools/calendar/calendar.db", "tools/notes/notes.db"} <= set(archive.namelist())
    archive.extract("custodian.db", tmp_path / "out")
    copy = sqlite3.connect(tmp_path / "out" / "custodian.db")
    assert copy.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 6
    copy.close()


def test_run_task(user_id, capsys):
    cli.main(["seed-demo", "--grant-reads"])
    assert cli.main(["run-task", "daily-summary"]) == 0
    out = capsys.readouterr().out
    assert "succeeded" in out and "Team standup" in out
    assert cli.main(["run-task", "nope"]) == 1


def test_rotate_keys(user_id, monkeypatch):
    memory_id = memory.write(user_id, "Rotated secret fact", "semantic")
    with db.connect() as conn:
        before = conn.execute("SELECT updated_at FROM memories WHERE id = ?", (memory_id,)).fetchone()[0]
    old_key = load_settings().app_secret_keys[0]
    new_key = crypto.generate_key()

    monkeypatch.setenv("APP_SECRET_KEY", f"{new_key},{old_key}")
    reset_settings()
    assert cli.main(["rotate-keys"]) == 0

    monkeypatch.setenv("APP_SECRET_KEY", new_key)  # the old key is gone
    reset_settings()
    stored = memory.get(user_id, memory_id)
    assert stored["content"] == "Rotated secret fact"
    with db.connect() as conn:
        after = conn.execute("SELECT updated_at FROM memories WHERE id = ?", (memory_id,)).fetchone()[0]
    assert after == before  # re-encrypting isn't an edit


def test_sandbox_selftest(capsys):
    assert cli.main(["sandbox-selftest"]) == 0
    assert "FAIL" not in capsys.readouterr().out
