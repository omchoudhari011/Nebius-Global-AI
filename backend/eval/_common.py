"""Shared setup for the eval scripts: each run uses a fresh, throwaway data directory, so
your real database is never touched. `--model nebius` takes NEBIUS_* from the environment
or backend/.env."""

from __future__ import annotations

import os
import re
import statistics
import sys
import tempfile
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
RESULTS = Path(__file__).resolve().parent / "results.md"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def setup(model: str | None = None) -> int:
    """Point Custodian at a temp data dir, create the schema, and return the owner's id."""
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env", override=False)  # NEBIUS_API_KEY etc. for --model nebius
    os.environ.update(
        {
            "CUSTODIAN_SKIP_DOTENV": "1",
            "CUSTODIAN_ENV": "development",
            "DATA_DIR": tempfile.mkdtemp(prefix="custodian-eval-"),
            "SCHEDULER_ENABLED": "false",
            "LOG_LEVEL": "WARNING",
        }
    )
    for name in ("DATABASE_PATH", "APP_SECRET_KEY", "SESSION_SECRET", "CUSTODIAN_PASSCODE"):
        os.environ.pop(name, None)  # throwaway dev secrets are generated in the temp dir
    if model:
        os.environ["MODEL_BACKEND"] = model

    from app import auth, db, skills
    from app.config import reset_settings

    reset_settings()
    db.init_db()
    user_id = auth.ensure_owner()
    skills.sync_from_files()
    return user_id


def backend_label() -> str:
    from app.model_backends import get_backend

    backend = get_backend()
    return f"{backend.name} ({backend.model_id})"


def p50_p95(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return statistics.median(values), cuts[94]


def update_results(label: str, value: str) -> None:
    """Replace the `- <label>: ...` line in results.md (or append it)."""
    line = f"- {label}: {value} _({date.today().isoformat()})_"
    text = RESULTS.read_text(encoding="utf-8")
    pattern = re.compile(rf"^- {re.escape(label)}:.*$", re.MULTILINE)
    text = pattern.sub(lambda _: line, text) if pattern.search(text) else text.rstrip() + "\n" + line + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(line)
