"""Runs one tool call in a separate, locked-down Python process.

app/tools/sandbox.py starts this file as `python -I -B runner.py` with a scrubbed
environment (no API or encryption keys) and the tool's own data directory as the working
directory. It reads one JSON request on stdin and writes one JSON reply on stdout:
{"ok": true, "result": ...} or {"ok": false, "error": "..."}.

Before the tool function runs, an audit hook (sys.addaudithook - it can't be removed
once added) blocks sockets and DNS (so: no network), starting processes, loading native
libraries through ctypes, and opening or changing files outside the tool's data
directory; reading Python's own files stays allowed so lazy imports work. On POSIX it
also caps CPU time, memory and file size. This hardens our own tool code against bad
arguments; it is not an OS-level sandbox, so the container should still run unprivileged.

Keep this file stdlib-only: it must not import app.config, app.db or app.crypto.
"""

from __future__ import annotations

import importlib
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ALLOWED_MODULE_PREFIX = "app.tools."
MAX_REQUEST_BYTES = 1_000_000

BLOCKED_EVENTS = frozenset(
    {
        "socket.__new__",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.startfile",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "pty.spawn",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ctypes.addressof",
        "ctypes.cdata",
        "ctypes.string_at",
        "ctypes.wstring_at",
        "webbrowser.open",
    }
)

# Events that change the filesystem, and which of their arguments are paths.
PATH_EVENTS = {
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.link": (0, 1),
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.rmdir": (0,),
    "os.symlink": (0, 1),
    "os.truncate": (0,),
    "os.utime": (0,),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
}

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _as_path(value: object) -> str | None:
    if value is None or isinstance(value, int):
        return None  # an already-open file descriptor
    try:
        return _norm(os.fsdecode(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "\0invalid"


def install_guard(data_dir: str) -> None:
    data_root = _norm(data_dir)
    read_roots = tuple(
        {_norm(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix) if p}
        | {_norm(os.path.join(BACKEND_DIR, "app"))}
    )

    def deny(what: str) -> None:
        raise PermissionError(f"sandbox: {what} is not allowed for tools")

    def hook(event: str, args: tuple) -> None:
        if event in BLOCKED_EVENTS:
            deny(event)
        elif event == "open":
            path = _as_path(args[0])
            if path is None or _inside(path, data_root):
                return
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & _WRITE_FLAGS)
            )
            if not writing and any(_inside(path, root) for root in read_roots):
                return
            deny("opening files outside the tool's data directory")
        elif event == "sqlite3.connect":
            database = args[0] if args else None
            if database in (":memory:", b":memory:", ""):
                return
            path = _as_path(database)
            if path is not None and not _inside(path, data_root):
                deny("opening databases outside the tool's data directory")
        elif event in PATH_EVENTS:
            for index in PATH_EVENTS[event]:
                path = _as_path(args[index]) if index < len(args) else None
                if path is not None and not _inside(path, data_root):
                    deny(f"{event} outside the tool's data directory")

    sys.addaudithook(hook)


def _limit_resources(timeout: float) -> None:
    try:
        import resource
    except ImportError:  # Windows: the wall-clock timeout in sandbox.py still applies
        return
    cpu = int(timeout) + 1
    for limit, value in (
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_AS, 1 << 30),
        (resource.RLIMIT_FSIZE, 50 << 20),
    ):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


def _reply(payload: dict) -> None:
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    sys.dont_write_bytecode = True
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        _reply({"ok": False, "error": "request too large"})
        return 1
    try:
        request = json.loads(raw.decode("utf-8"))
        module_name = request["module"]
        function_name = request["function"]
        args = request["args"]
        ctx = request["context"]
        timeout = float(request.get("timeout", 5))
    except (ValueError, KeyError, TypeError) as exc:
        _reply({"ok": False, "error": f"bad sandbox request: {exc}"})
        return 1

    if not module_name.startswith(ALLOWED_MODULE_PREFIX) or function_name.startswith("_"):
        _reply({"ok": False, "error": "that tool function can't be run"})
        return 1

    data_dir = ctx["data_dir"]
    os.makedirs(data_dir, exist_ok=True)
    os.chdir(data_dir)
    sys.path.insert(0, BACKEND_DIR)

    try:
        module = importlib.import_module(module_name)
        fn = getattr(module, function_name)
        if not callable(fn):
            raise AttributeError(function_name)
        from zoneinfo import ZoneInfo

        ZoneInfo(ctx.get("timezone", "UTC"))  # load the zone file now, before files are locked down
    except Exception as exc:  # noqa: BLE001 - reported back to the gate
        _reply({"ok": False, "error": f"can't load tool: {type(exc).__name__}: {exc}"})
        return 1

    _limit_resources(timeout)
    install_guard(data_dir)

    try:
        result = fn(ctx, **args)
    except Exception as exc:  # noqa: BLE001 - reported back to the gate
        _reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 0
    _reply({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
