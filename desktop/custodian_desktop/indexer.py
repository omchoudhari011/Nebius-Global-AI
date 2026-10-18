"""Builds the index for "search inside my files", when you switch it on in the app.

Every 30 minutes (and soon after start-up, or when the server says "index now") it asks the
server what to do (GET /desktop/index/config): off, or which folders. It walks those folders,
takes the text out of the files it can read - text and code, PDF, Word, PowerPoint, Excel,
OpenDocument - and sends only new or changed files' text, cut into passages, plus the paths that
are gone. Nothing else leaves the PC: no file bytes, no images.

It skips what shouldn't be read: hidden and system folders, program and package folders
(node_modules, .git, venvs...), files whose names say they hold secrets (passwords, keys,
recovery codes, wallets), files over the size limits, and anything it can't read. The server
also drops any passage that looks like it holds a password or card number.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import zipfile
from html import unescape
from pathlib import Path

log = logging.getLogger(__name__)

EVERY_SECONDS = 30 * 60
CHUNK_CHARS, OVERLAP = 1200, 150
MAX_TEXT_CHARS = 150_000  # of one file
MAX_TEXT_BYTES = 3 * 1024 * 1024
MAX_DOC_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 300
SEND_CHARS = 3_000_000  # about this much text per request
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".yaml",
    ".yml", ".ini", ".toml", ".tex", ".sql", ".kt", ".swift", ".r", ".srt", ".rtf", ".ipynb",
}
DOC_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods"}
SKIP_DIRS = {"appdata", "node_modules", ".git", "$recycle.bin", "windows", ".venv", "venv", "env", "__pycache__",
             "site-packages", "anaconda3", "miniconda3", "envs", ".cache", ".conda", "program files", "program files (x86)",
             "programdata", "build", "dist", ".next", "target", "bin", "obj", ".idea", ".vscode", "packages", "vendor"}
SECRET_NAMES = re.compile(
    r"(?i)password|passwd|passcode|secret|credential|private[_ -]?key|id_rsa|id_ed25519|\.env$|^\.env|\.pem$|\.key$|\.kdbx?$"
    r"|\.pfx$|\.p12$|\.keystore$|wallet|seed[_ -]?phrase|mnemonic|recovery[_ -]?codes?|backup[_ -]?codes?|2fa|otp|token"
)


# Taking the text out ------------------------------------------------------------------------------

def _xml_text(xml: str) -> str:
    xml = re.sub(r"</(?:w:p|a:p|text:p|text:h|row)>", "\n", xml)
    return unescape(re.sub(r"<[^>]+>", " ", xml))


def _zip_text(path: Path, kind: str) -> str:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if kind == ".docx":
            parts = [n for n in names if re.fullmatch(r"word/(?:document|footnotes|endnotes)\d*\.xml", n)]
        elif kind == ".pptx":
            parts = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        elif kind == ".xlsx":  # the words in a spreadsheet: shared strings and sheet names
            parts = [n for n in names if n in ("xl/sharedStrings.xml", "xl/workbook.xml")]
        else:  # OpenDocument
            parts = [n for n in names if n == "content.xml"]
        return "\n".join(_xml_text(archive.read(n)[:20_000_000].decode("utf-8", "replace")) for n in parts)


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        return ""
    pages = []
    for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one broken page shouldn't lose the rest
            continue
        if text.strip():
            pages.append(f"[page {number}] {text.strip()}")
        if sum(len(p) for p in pages) > MAX_TEXT_CHARS:
            break
    return "\n".join(pages)


def _plain_text(path: Path) -> str:
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    if b"\x00" in data[:4096]:
        return ""
    text = data.decode("utf-8", "replace")
    if path.suffix.lower() in (".html", ".htm", ".xml"):
        text = _xml_text(re.sub(r"(?is)<(script|style|svg|head)\b.*?</\1>", " ", text))
    elif path.suffix.lower() == ".rtf":
        text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", text)
    return text


def extract(path: Path) -> str:
    """The readable text of a file, or "" when there's none (or it can't be read)."""
    kind = path.suffix.lower()
    try:
        if kind in TEXT_EXTENSIONS:
            text = _plain_text(path)
        elif kind == ".pdf":
            text = _pdf_text(path)
        elif kind in DOC_EXTENSIONS:
            text = _zip_text(path, kind)
        else:
            return ""
    except Exception as exc:  # noqa: BLE001 - a locked, broken or odd file: skip it
        log.debug("couldn't read %s: %s", path, exc)
        return ""
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)[:MAX_TEXT_CHARS]


def chunks(text: str) -> list[str]:
    """Passages of about CHUNK_CHARS, cut at a line or sentence end where possible, overlapping a little."""
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            cut = max(text.rfind("\n", start + CHUNK_CHARS // 2, end), text.rfind(". ", start + CHUNK_CHARS // 2, end))
            end = cut + 1 if cut > start else end
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return out


# Finding the files --------------------------------------------------------------------------------

def resolve_folder(raw: str) -> Path | None:
    """'~/Documents' -> the real folder, including Windows' OneDrive copies of Documents and Desktop."""
    path = Path(os.path.expandvars(os.path.expanduser(raw.strip()))).resolve()
    if path.is_dir():
        return path
    home = Path.home()
    try:
        moved = home / "OneDrive" / path.relative_to(home)
    except ValueError:
        return None
    return moved if moved.is_dir() else None


def wanted(path: Path) -> bool:
    kind = path.suffix.lower()
    return (kind in TEXT_EXTENSIONS or kind in DOC_EXTENSIONS) and not SECRET_NAMES.search(path.name) \
        and not path.name.startswith(("~$", "."))


def walk(folders: list[str], limit: int) -> dict[str, tuple[float, int]]:
    """{path: (modified, size)} of the readable files in `folders`, at most `limit` of them."""
    found: dict[str, tuple[float, int]] = {}
    for raw in folders:
        root = resolve_folder(raw)
        if root is None:
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith((".", "$", "~"))
                       and not SECRET_NAMES.search(d)]
            for name in files:
                path = Path(current) / name
                if not wanted(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                limit_bytes = MAX_TEXT_BYTES if path.suffix.lower() in TEXT_EXTENSIONS else MAX_DOC_BYTES
                if stat.st_size == 0 or stat.st_size > limit_bytes:
                    continue
                found[str(path)] = (round(stat.st_mtime, 2), stat.st_size)
                if len(found) >= limit:
                    return found
    return found


def plan(on_disk: dict[str, tuple[float, int]], indexed: dict[str, list]) -> tuple[list[str], list[str]]:
    """(paths to (re)send, paths to forget): new or changed files, and indexed ones now gone."""
    send = [p for p, (modified, size) in on_disk.items()
            if p not in indexed or abs(float(indexed[p][0]) - modified) > 1 or int(indexed[p][1]) != size]
    gone = [p for p in indexed if p not in on_disk]
    return send, gone


# Running ---------------------------------------------------------------------------------------------

class Indexer:
    """The background thread. `client` is the app's server client (client.py)."""

    def __init__(self, client_getter) -> None:  # noqa: ANN001 - returns the current Client
        self.client_getter = client_getter
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.last: dict = {}

    def index_now(self) -> None:
        self.wake.set()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()

    def run(self) -> None:
        _lower_priority()
        self.wake.wait(60)  # let the app finish starting first
        while not self.stopping.is_set():
            self.wake.clear()
            try:
                self.last = self.once()
            except Exception as exc:  # noqa: BLE001 - server down, signed out: try again later
                log.info("indexing skipped: %s", exc)
            self.wake.wait(EVERY_SECONDS)

    def once(self) -> dict:
        client = self.client_getter()
        config = client.index_config()
        if not config.get("enabled"):
            return {"enabled": False}
        on_disk = walk(config.get("folders") or [], int(config.get("max_files") or 20_000))
        send, gone = plan(on_disk, config.get("indexed") or {})
        sent = 0
        batch, size = [], 0
        per_file = int(config.get("max_chunks_per_file") or 120)
        batch_files = int(config.get("batch") or 40)
        for path in send:
            if self.stopping.is_set():
                break
            text = extract(Path(path))
            modified, file_size = on_disk[path]
            pieces = chunks(text)[:per_file] if text else []
            batch.append({"path": path, "name": Path(path).name, "kind": Path(path).suffix.lower().lstrip("."),
                          "modified": modified, "size": file_size, "chunks": pieces})
            size += sum(len(p) for p in pieces)
            if len(batch) >= batch_files or size >= SEND_CHARS:
                client.index_files(batch, gone)
                sent, batch, size, gone = sent + len(batch), [], 0, []
            time.sleep(0.01)  # stay out of the way of whatever you're doing
        if batch or gone:
            client.index_files(batch, gone)
            sent += len(batch)
        log.info("indexed %d new or changed files (%d on disk)", sent, len(on_disk))
        return {"enabled": True, "files": len(on_disk), "sent": sent}


def _lower_priority() -> None:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -2)  # THREAD_PRIORITY_LOWEST
    except (AttributeError, OSError):
        pass
