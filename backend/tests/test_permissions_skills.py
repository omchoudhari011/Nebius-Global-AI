import shutil
import sqlite3

import pytest

from app import db, permissions, skills


def test_default_deny(user_id, tool_ids):
    assert permissions.check(user_id, tool_ids["calendar"], "read") is False
    rows = permissions.list_for_user(user_id)
    assert len(rows) == 4 and not any(r["granted"] for r in rows)


def test_grant_revoke_and_history(user_id, tool_ids):
    cal = tool_ids["calendar"]
    permissions.grant(user_id, cal, "read")
    assert permissions.check(user_id, cal, "read")
    assert not permissions.check(user_id, cal, "write")  # scopes are separate
    permissions.grant(user_id, cal, "read")  # no change -> no new event
    permissions.revoke(user_id, cal, "read")
    assert not permissions.check(user_id, cal, "read")
    history = permissions.events(user_id)
    assert [e["granted"] for e in history] == [False, True]


def test_unknown_scope(user_id, tool_ids):
    with pytest.raises(permissions.UnknownScope):
        permissions.grant(user_id, tool_ids["calendar"], "delete")


def test_skills_are_synced_once(user_id):
    names = {s["name"]: s for s in skills.list_skills()}
    assert set(names) == {"general", "calendar", "notes", "daily-summary"}
    assert all(s["version"] == 1 for s in names.values())
    assert "calendar:read" in names["daily-summary"]["tools"]
    assert "calendar:write" not in names["daily-summary"]["tools"]
    assert skills.sync_from_files() == []


def test_changed_file_becomes_a_new_version(user_id, tmp_path, monkeypatch):
    folder = tmp_path / "skills"
    shutil.copytree(skills.SKILLS_DIR, folder)
    path = folder / "notes.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAlways use bullet points.\n", encoding="utf-8")
    monkeypatch.setattr(skills, "SKILLS_DIR", folder)
    skills.definitions.cache_clear()
    try:
        assert skills.sync_from_files() == [("notes", 2)]
        assert skills.load_skill("notes").version == 2
        assert skills.get_skill(skills.load_skill("notes").id).tools == frozenset({("notes", "read"), ("notes", "write")})
    finally:
        skills.definitions.cache_clear()


def test_saved_versions_are_immutable(user_id):
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), db.connect() as conn:
        conn.execute("UPDATE skills SET prompt_template = 'hacked' WHERE name = 'general'")
    with db.connect() as conn:  # rolling back is allowed
        conn.execute("UPDATE skills SET active = 0 WHERE name = 'notes'")
    assert skills.load_skill("notes") is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("What's on my calendar tomorrow?", "calendar"),
        ("Take a note about the plumber", "notes"),
        ("Give me a summary of my day", "daily-summary"),
        ("Tell me a joke", "general"),
        ("Add a note about the calendar sync", "general"),  # tie -> general
    ],
)
def test_pick_skill(message, expected):
    assert skills.pick_skill_name(message) == expected


def test_bad_skill_file(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("no front matter", encoding="utf-8")
    with pytest.raises(skills.SkillFileError):
        skills.parse_skill_file(bad)
