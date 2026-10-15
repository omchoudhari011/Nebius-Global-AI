"""Files you give Custodian: PDFs, text files and photos, turned into text the model can search.

    POST /documents   {name, content_type, data: base64}  -> extracted and stored
    GET  /documents   your files;  DELETE /documents/{id}

Text comes from the file itself (PDF text layer, or the text file); a photo is described by a
vision model when VISION_MODEL is set (e.g. `llava` in Ollama, on this machine), including
any text in it. Without one, photos are refused rather than stored without meaning.

The text is stored in the documents tool's own database (app/tools/documents_tool.py), where
the model reaches it only through the Files · read switch.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import os
import re

from html import unescape

import httpx

from . import sensitive
from .config import get_settings
from .tools import documents_tool

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 400_000  # ~100 parts of 4,000; more than that is cut
TEXT_TYPES = ("text/", "application/json", "application/csv", "application/xml", "application/xhtml+xml")
TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".log", ".html", ".htm", ".xml")
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
VISION_PROMPT = (
    "Describe this image for someone who can't see it, in a few sentences. Then copy out every piece of "
    "text you can read in it exactly, under the heading 'Text in the image:'. Say 'none' if there is no text."
)

_transport: httpx.BaseTransport | None = None  # tests swap in a mock


class DocumentError(ValueError):
    """The file can't be used; the message is safe to show."""


def _folder():  # noqa: ANN202 - a Path
    folder = get_settings().tool_data_dir / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("That PDF is password-protected; save an unprotected copy and upload that.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise DocumentError(f"That PDF couldn't be read ({exc}).") from None
    text = "\n\n".join(f"[page {i}]\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
    if not text:
        raise DocumentError("That PDF has no text layer (it's probably scanned); upload the pages as photos instead.")
    return text


def _image_text(data: bytes, content_type: str) -> str:
    model = os.getenv("VISION_MODEL", "").strip()
    if not model:
        raise DocumentError("Reading photos needs a vision model: set VISION_MODEL (e.g. llava in Ollama) on the server.")
    settings = get_settings()
    base = (os.getenv("VISION_BASE_URL", "").strip() or settings.nebius_base_url).rstrip("/")
    key = os.getenv("VISION_API_KEY", "").strip() or (settings.nebius_api_key if base == settings.nebius_base_url.rstrip("/") else "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
    payload = {"model": model, "max_tokens": 600, "temperature": 0, "frequency_penalty": 0.6,
               "messages": [{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT},
                                                         {"type": "image_url", "image_url": {"url": url}}]}]}
    try:
        with httpx.Client(timeout=180, transport=_transport) as http:
            resp = http.post(f"{base}/chat/completions", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise DocumentError(f"The vision model didn't answer ({type(exc).__name__}).") from None
    if resp.status_code >= 400:
        raise DocumentError(f"The vision model returned {resp.status_code}.")
    text = _without_loops((resp.json()["choices"][0]["message"].get("content") or "").strip())
    if not text:
        raise DocumentError("The vision model couldn't describe that photo.")
    return text


def _without_loops(text: str) -> str:
    """Small vision models sometimes repeat one line until they run out of tokens; keep at most
    two copies of any run of identical lines."""
    out: list[str] = []
    for line in text.splitlines():
        if len(out) >= 2 and line.strip() and line == out[-1] == out[-2]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def _sniff(data: bytes) -> str | None:
    """The file's real type from its first bytes, whatever its name or declared type says."""
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"GIF8":
        return "image/gif"
    return None


def extract(name: str, content_type: str, data: bytes) -> tuple[str, str]:
    """(kind, text) for an uploaded file, judged by its content first. Raises DocumentError."""
    declared = (content_type or "").split(";")[0].strip().lower()
    lower = name.lower()
    actual = _sniff(data)
    if actual == "application/pdf":
        return "pdf", _pdf_text(data)
    if actual in IMAGE_TYPES:
        return "image", _image_text(data, actual)
    if declared == "application/pdf" or lower.endswith(".pdf"):
        if b"\x00" in data[:1024]:
            raise DocumentError("That file isn't a valid PDF.")
    elif declared in IMAGE_TYPES or lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        raise DocumentError("That file isn't a PNG, JPEG, WebP or GIF image.")
    elif not (declared.startswith(TEXT_TYPES) or lower.endswith(TEXT_EXTENSIONS)) or b"\x00" in data[:1024]:
        raise DocumentError("Custodian can read PDFs, text and web pages, and photos (PNG, JPEG, WebP).")
    try:  # plain text, including a text file that was named or sent as a PDF
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if declared in ("text/html", "application/xhtml+xml") or lower.endswith((".html", ".htm")):
        return "text", _html_text(text)
    return "text", text


def _html_text(html: str) -> str:
    """The readable text of a web page: no scripts, styles or tags, one line per block."""
    html = re.sub(r"(?is)<(script|style|svg|noscript|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article|header|footer|pre)>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def get(user_id: int, doc_id: int) -> dict | None:
    """One file with its full text, or None."""
    folder = get_settings().tool_data_dir / "documents"
    if not (folder / "documents.db").exists():
        return None
    conn = documents_tool.connect(folder)
    try:
        row = conn.execute("SELECT * FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
    finally:
        conn.close()
    return {**_row(row), "text": row["text"]} if row else None


def add(user_id: int, name: str, content_type: str, data_b64: str) -> dict:
    try:
        data = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise DocumentError("The file wasn't sent correctly; try again.") from None
    if not data:
        raise DocumentError("That file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"Files can be up to {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    name = os.path.basename(name.replace("\\", "/")).strip()[:200] or "file"
    kind, text = extract(name, content_type, data)
    text = text.strip()[:MAX_TEXT_CHARS]
    if not text:
        raise DocumentError("No text could be found in that file.")
    secret = sensitive.find(text)
    conn = documents_tool.connect(_folder())
    try:
        with conn:
            doc_id = conn.execute(
                "INSERT INTO documents (user_id, name, kind, bytes, text) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, kind, len(data), text),
            ).lastrowid
            conn.execute("INSERT INTO documents_fts (rowid, name, text) VALUES (?, ?, ?)", (doc_id, name, text))
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    finally:
        conn.close()
    log.info("stored a %s document (%d characters)", kind, len(text))
    out = _row(row)
    out["warning"] = (
        f"It looks like it contains {secret}. Custodian keeps it so you can ask about it, but delete it if you'd rather not."
        if secret else None
    )
    return out


def _row(row) -> dict:  # noqa: ANN001 - sqlite3.Row
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "bytes": row["bytes"],
        "characters": len(row["text"]),
        "preview": row["text"][:240],
        "created_at": row["created_at"].replace(" ", "T") + "Z",
    }


def list_for(user_id: int) -> list[dict]:
    folder = get_settings().tool_data_dir / "documents"
    if not (folder / "documents.db").exists():
        return []
    conn = documents_tool.connect(folder)
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM documents WHERE user_id = ? ORDER BY id DESC", (user_id,))]
    finally:
        conn.close()


def delete(user_id: int, doc_id: int) -> bool:
    folder = get_settings().tool_data_dir / "documents"
    if not (folder / "documents.db").exists():
        return False
    conn = documents_tool.connect(folder)
    try:
        with conn:
            removed = conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)).rowcount
        if removed:
            conn.execute("INSERT INTO documents_fts (documents_fts) VALUES ('optimize')")
            conn.commit()
    finally:
        conn.close()
    return removed > 0
