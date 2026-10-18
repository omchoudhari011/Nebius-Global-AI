"""Learning from your corrections overnight.

The 👍/👎 buttons and "what it should have said" (app/feedback.py) become training data; every
night, while the PC is idle, the desktop app runs backend/finetune/nightly.py, which:

1. asks here whether there's anything new (status(): at least MIN_NEW shared ratings since the
   last run, and "nightly_learning" on),
2. downloads the data (training_data(): the replies to keep, and each correction as a pair -
   your correction preferred over the reply it replaced),
3. continues training the current adapter on it, with a sample of the usual conversations so
   nothing else is forgotten, and compares the result with the model you use,
4. switches only if it's at least as good, and reports back here (record()): you get a
   notification in the morning either way.

The record lives in DATA_DIR/learning.json - only times, counts and the outcome, never text.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from . import db, feedback, notifications, settings_store
from .config import get_settings

MIN_NEW = 3  # fewer new ratings than this isn't worth a night's training
OUTCOMES = ("switched", "kept", "skipped", "stopped", "failed")
_lock = threading.Lock()


def _path():  # noqa: ANN202 - a Path
    return get_settings().data_dir / "learning.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def status(user_id: int) -> dict:
    with _lock:
        mine = _load().get(str(user_id), {})
    new = feedback.shared_since(user_id, mine.get("trained_through"))
    enabled = bool(settings_store.get(user_id)["nightly_learning"])
    return {"enabled": enabled, "new_examples": new, "due": enabled and new >= MIN_NEW, "min_new": MIN_NEW,
            "last_run": mine.get("last_run")}


def training_data(user_id: int) -> dict:
    """What tonight's run trains on, with the database time it covers up to (sent back in record())."""
    with db.connect() as conn:
        now = conn.execute("SELECT datetime('now')").fetchone()[0]
    return {"through": now, "examples": feedback.training_examples(user_id), "pairs": feedback.preference_pairs(user_id)}


def record(user_id: int, outcome: str, summary: str, *, through: str | None = None, examples: int = 0) -> dict:
    """The nightly run's result. "switched" and "kept" mark the data up to `through` as learned;
    "stopped" (you came back to the PC) and "failed" leave it for the next night."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    summary = " ".join(summary.split())[:400]
    run = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "outcome": outcome, "summary": summary,
           "examples": int(examples)}
    with _lock:
        state = _load()
        mine = state.setdefault(str(user_id), {})
        mine["last_run"] = run
        if outcome in ("switched", "kept") and through:
            mine["trained_through"] = through
        _save(state)
    if outcome in ("switched", "kept", "failed"):
        title = {"switched": f"I learned from {examples} of your ratings overnight",
                 "kept": "I practised on your corrections overnight, but kept the current model",
                 "failed": "Last night's learning run didn't finish"}[outcome]
        notifications.add(user_id, "learning", title, summary or None, ref="learning")
    return run
