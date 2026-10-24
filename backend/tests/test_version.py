"""Which Custodian this is, and whether an update is waiting - git stood in for, so no network."""

import pytest

from app import version


@pytest.fixture
def git(monkeypatch):
    calls = []
    answers = {"log -1": "61cddc7\t2026-10-24T12:00:00+05:30\tAutomatic encrypted backups",
               "fetch": "", "rev-list": "2", "log --format=%s": "Faster replies\nA new HUD"}

    def fake(*args, timeout=20):
        calls.append(args)
        key = next(k for k in answers if " ".join(args).startswith(k))
        if key == "fetch" and answers.get("offline"):
            raise RuntimeError("fatal: could not read Username")
        return answers[key]

    monkeypatch.setattr(version, "_git", fake)
    version._checked.clear()
    version._checking.clear()
    yield answers, calls
    version._checked.clear()


def test_an_update_is_found_and_listed(client, git):
    first = client.get("/version?refresh=true&wait=true").json()
    assert first["commit"] == "61cddc7" and first["update_available"] is True and first["behind"] == 2
    assert first["new"] == ["Faster replies", "A new HUD"] and first["error"] is None


def test_a_check_that_cant_reach_github_says_so_and_never_asks_for_a_sign_in(client, git):
    answers, _ = git
    answers["offline"] = True
    out = client.get("/version?refresh=true&wait=true").json()
    assert out["update_available"] is False and "couldn't reach GitHub" in out["error"]
    import os

    assert os.environ.get("GIT_TERMINAL_PROMPT") is None  # set only for git itself, in _git


def test_the_check_runs_at_most_every_few_hours(client, git):
    _, calls = git
    client.get("/version?refresh=true&wait=true")
    fetches = sum(1 for c in calls if c[0] == "fetch")
    client.get("/version")  # fresh enough: no new fetch
    version.wait_for_check()
    assert sum(1 for c in calls if c[0] == "fetch") == fetches == 1
