"""Probes that try what the sandbox must stop. Used by eval/test_security.py and
`python -m app.cli sandbox-selftest`; never registered as a tool the model can call.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def try_network(ctx: dict) -> str:
    import urllib.request

    urllib.request.urlopen("http://example.com", timeout=2)
    return "network reachable"


def try_read_file(ctx: dict, path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read(100)


def try_write_file(ctx: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("sandbox probe")
    return "written"


def try_subprocess(ctx: dict) -> str:
    import subprocess

    subprocess.run(["python", "--version"], check=False)
    return "process started"


def read_env(ctx: dict) -> dict:
    return dict(os.environ)


def sleep(ctx: dict, seconds: float) -> str:
    time.sleep(seconds)
    return "slept"


def write_inside(ctx: dict) -> str:
    probe = Path(ctx["data_dir"]) / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    return probe.read_text(encoding="utf-8")
