"""When the overnight learning run starts and stops (nothing here trains or runs a process)."""

import datetime as dt

from custodian_desktop.learning import IDLE_TO_START, NightlyLearner


class _Client:
    token = "t"

    def __init__(self, due=True):
        self.due, self.asked, self.results = due, 0, []

    def learning_status(self):
        self.asked += 1
        return {"due": self.due, "new_examples": 5}

    def learning_result(self, outcome, summary):
        self.results.append((outcome, summary))


class _Process:
    def __init__(self):
        self.returncode, self.stopped, self.pid = None, False, 123

    def poll(self):
        return self.returncode

    def terminate(self):
        self.stopped = True
        self.returncode = -15


def _learner(client, *, hour=2, idle=IDLE_TO_START + 5, plugged=True):
    state = {"now": dt.datetime(2026, 10, 19, hour, 10), "idle": idle}
    spawned = []

    def spawn():
        spawned.append(_Process())
        return spawned[-1]

    learner = NightlyLearner(lambda: client, lambda: "http://127.0.0.1:8000", lambda: state["idle"],
                             now=lambda: state["now"], plugged_in=lambda: plugged, spawn=spawn)
    return learner, state, spawned


def test_it_starts_at_night_when_idle_plugged_in_and_theres_something_new(monkeypatch):
    commands = []
    monkeypatch.setattr("custodian_desktop.learning.subprocess.run", lambda cmd, **k: commands.append(cmd))
    client = _Client()
    learner, state, spawned = _learner(client)
    learner.tick()
    assert len(spawned) == 1
    learner.tick()  # still running, still idle: left alone
    assert not spawned[0].stopped and commands == []
    state["idle"] = 3  # a key press
    learner.tick()
    assert ["docker", "stop", "-t", "5", "custodian-nightly"] in commands  # the training container
    killed = ["taskkill", "/T", "/F", "/PID", "123"] in commands  # Windows: the run and everything it started
    assert (killed or spawned[0].stopped) and client.results[0][0] == "stopped"
    state["idle"] = IDLE_TO_START + 5
    learner.tick()  # once a night, even after being stopped
    assert len(spawned) == 1
    state["now"] = dt.datetime(2026, 10, 20, 1, 30)  # the next night
    learner.tick()
    assert len(spawned) == 2


def test_it_waits_for_night_idleness_power_and_something_new():
    for kwargs in ({"hour": 14}, {"idle": 120}, {"plugged": False}):
        client = _Client()
        learner, _, spawned = _learner(client, **kwargs)
        learner.tick()
        assert spawned == [] and client.asked == 0, kwargs
    client = _Client(due=False)
    learner, _, spawned = _learner(client)
    learner.tick()
    learner.tick()
    assert spawned == [] and client.asked == 1  # asked once tonight, then left till tomorrow


def test_a_finished_run_is_tidied_up_without_reporting_twice():
    client = _Client()
    learner, _, spawned = _learner(client)
    learner.tick()
    spawned[0].returncode = 0  # nightly.py reported its own result
    learner.tick()
    assert learner.process is None and client.results == []
