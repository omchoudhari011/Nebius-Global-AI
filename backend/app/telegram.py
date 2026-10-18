"""Talking to Custodian on Telegram: a bot of your own that only your chat can use.

Set TELEGRAM_BOT_TOKEN (from @BotFather), press "Link Telegram" in Tools & Data and send the bot
"/link <code>" from your own account. After that:

- every message (or voice note, with a Whisper server) from that chat is a chat turn, kept in
  one conversation per chat ("/new" starts another);
- a PC action that needs your OK asks right there, with Allow and Deny buttons
  (app/approvals.py), for up to two minutes - denied or unanswered, it never reaches the PC;
- reminders, meeting heads-ups, watcher alerts and finished tasks arrive as messages too
  ("telegram_alerts" in the settings).

Any other chat gets a one-line refusal and nothing else; the code is single-use, expires after
10 minutes, and a chat that guesses wrong five times is ignored. The bot polls Telegram
(getUpdates), so the server needs no public address. Messages pass through Telegram's servers:
"Where your data goes" lists it once linked.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time

import httpx

from . import approvals, db
from .crypto import decrypt, encrypt

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
LINK_SECONDS = 600
MAX_WRONG_CODES = 5
MAX_MESSAGE = 4000  # Telegram's limit is 4096 characters
ALERT_KINDS = ("reminder", "meeting", "watch", "task", "learning")

_transport: httpx.BaseTransport | None = None  # tests swap in a mock
_lock = threading.Lock()
_codes: dict[str, tuple[int, float]] = {}  # code -> (user id, expiry)
_wrong: dict[int, int] = {}  # chat id -> wrong codes sent
_conversations: dict[int, int] = {}  # chat id -> conversation id
_sent: dict[int, tuple[int, int]] = {}  # approval id -> (chat id, message id), to edit after a decision
_stop = threading.Event()
_thread: threading.Thread | None = None
_bot_name: str | None = None


class TelegramError(RuntimeError):
    """Not configured, or Telegram refused; the message is safe to show."""


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def configured() -> bool:
    return bool(_token())


def _call(method: str, timeout: float = 15, **payload) -> dict:  # noqa: ANN003
    token = _token()
    if not token:
        raise TelegramError("Telegram isn't set up on this server: set TELEGRAM_BOT_TOKEN (see README).")
    with httpx.Client(timeout=timeout + 5, transport=_transport) as http:
        resp = http.post(f"{API}/bot{token}/{method}", json=payload)
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code >= 400 or not data.get("ok"):
        raise TelegramError(f"Telegram refused {method}: {data.get('description') or resp.status_code}")
    return data.get("result")


def bot_name() -> str | None:
    global _bot_name
    if _bot_name is None:
        try:
            _bot_name = (_call("getMe") or {}).get("username")
        except (TelegramError, httpx.HTTPError):
            return None
    return _bot_name


# Linking your chat -------------------------------------------------------------------------------

def link_code(user_id: int) -> dict:
    if not configured():
        raise TelegramError("Telegram isn't set up on this server: set TELEGRAM_BOT_TOKEN (see README).")
    code = secrets.token_hex(4).upper()  # 8 characters: guessing it is hopeless in 10 minutes
    with _lock:
        for old in [c for c, (u, _) in _codes.items() if u == user_id]:
            _codes.pop(old)
        _codes[code] = (user_id, time.monotonic() + LINK_SECONDS)
    name = bot_name()
    return {"code": code, "bot": name, "expires_in": LINK_SECONDS,
            "how": f"Send “/link {code}” to @{name or 'your bot'} on Telegram from your own account."}


def _link(chat_id: int, code: str, who: str | None) -> int | None:
    with _lock:
        if _wrong.get(chat_id, 0) >= MAX_WRONG_CODES:
            return None
        found = _codes.get(code.strip().upper())
        if found is None or found[1] < time.monotonic():
            _wrong[chat_id] = _wrong.get(chat_id, 0) + 1
            return None
        _codes.pop(code.strip().upper())
    user_id = found[0]
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO integrations (user_id, provider, account, scopes, refresh_token)
            VALUES (?, 'telegram', ?, 'chat', ?)
            ON CONFLICT (user_id, provider) DO UPDATE SET account = excluded.account,
              refresh_token = excluded.refresh_token, created_at = datetime('now')
            """,
            (user_id, who, encrypt(str(chat_id))),
        )
    return user_id


def chat_for(user_id: int) -> int | None:
    with db.connect() as conn:
        row = conn.execute("SELECT refresh_token FROM integrations WHERE user_id = ? AND provider = 'telegram'",
                           (user_id,)).fetchone()
    return int(decrypt(row["refresh_token"])) if row else None


def user_for(chat_id: int) -> int | None:
    with db.connect() as conn:
        rows = conn.execute("SELECT user_id, refresh_token FROM integrations WHERE provider = 'telegram'").fetchall()
    return next((r["user_id"] for r in rows if decrypt(r["refresh_token"]) == str(chat_id)), None)


def status(user_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT account, created_at FROM integrations WHERE user_id = ? AND provider = 'telegram'",
                           (user_id,)).fetchone()
    return {"provider": "telegram", "label": "Telegram", "configured": configured(), "connected": row is not None,
            "account": row["account"] if row else None, "connected_at": db.iso(row["created_at"]) if row else None}


# Sending -----------------------------------------------------------------------------------------

def send(chat_id: int, text: str, **extra) -> dict | None:  # noqa: ANN003
    text = text.strip() or "…"
    last = None
    for start in range(0, len(text), MAX_MESSAGE):
        last = _call("sendMessage", chat_id=chat_id, text=text[start:start + MAX_MESSAGE],
                     **(extra if start + MAX_MESSAGE >= len(text) else {}))
    return last


def notify(user_id: int, kind: str, title: str, body: str | None = None) -> bool:
    """Forward a reminder or alert to your Telegram chat, when one is linked and alerts are on."""
    if kind not in ALERT_KINDS or not configured():
        return False
    from . import settings_store

    chat = chat_for(user_id)
    if chat is None or not settings_store.get(user_id).get("telegram_alerts", True):
        return False
    icon = {"reminder": "⏰", "meeting": "📅", "watch": "👀", "task": "✅", "learning": "🧠"}[kind]
    try:
        send(chat, f"{icon} {title}" + (f"\n{body}" if body else ""))
    except (TelegramError, httpx.HTTPError) as exc:
        log.warning("couldn't send a Telegram alert: %s", exc)
        return False
    return True


def _ask(approval: approvals.Approval) -> None:
    """approvals.on_request: a PC action asked for on Telegram gets its Allow and Deny buttons."""
    if approval.origin != "telegram":
        return
    chat = chat_for(approval.user_id)
    if chat is None:
        return
    sent = send(chat, f"🔐 {approval.title}\n\n{approval.details}\n\nAllow this on your PC? (2 minutes)",
                reply_markup={"inline_keyboard": [[{"text": "Allow once", "callback_data": f"ok:{approval.id}"},
                                                    {"text": "Deny", "callback_data": f"no:{approval.id}"}]]})
    if sent:
        _sent[approval.id] = (chat, sent["message_id"])


# Receiving ---------------------------------------------------------------------------------------

def _transcribe(message: dict, user_id: int) -> str | None:
    from . import settings_store, voice

    voice_note = message.get("voice") or message.get("audio")
    if not voice_note or not voice.configured():
        return None
    path = (_call("getFile", file_id=voice_note["file_id"]) or {}).get("file_path")
    if not path:
        return None
    with httpx.Client(timeout=60, transport=_transport) as http:
        audio = http.get(f"{API}/file/bot{_token()}/{path}").content
    spoken = settings_store.get(user_id)["voice_language"]
    return voice.transcribe(audio, voice_note.get("mime_type") or "audio/ogg", spoken if spoken in voice.LANGUAGES else None)


def _chat_turn(user_id: int, chat_id: int, text: str) -> None:
    from . import orchestrator

    try:
        _call("sendChatAction", chat_id=chat_id, action="typing")
    except (TelegramError, httpx.HTTPError):
        pass
    try:
        try:
            result = orchestrator.handle(user_id, text, conversation_id=_conversations.get(chat_id), origin="telegram")
        except orchestrator.ConversationNotFound:
            result = orchestrator.handle(user_id, text, origin="telegram")
        _conversations[chat_id] = result["conversation_id"]
        send(chat_id, result["reply"])
    except Exception as exc:  # noqa: BLE001 - say so in the chat rather than going quiet
        log.exception("a Telegram turn failed")
        try:
            send(chat_id, f"Sorry, that didn't work: {str(exc)[:300]}")
        except (TelegramError, httpx.HTTPError):
            pass


def handle_update(update: dict) -> None:
    """One update from getUpdates. Chat turns run on their own thread, so the poll keeps going
    and an Allow pressed while a turn waits on it is received."""
    callback = update.get("callback_query")
    if callback:
        chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
        user_id = user_for(chat_id) if chat_id is not None else None
        verb, _, raw_id = str(callback.get("data", "")).partition(":")
        decided = False
        if user_id is not None and verb in ("ok", "no") and raw_id.isdigit():
            decided = approvals.decide(user_id, int(raw_id), verb == "ok")
        note = ("Allowed" if verb == "ok" else "Denied") if decided else "That request is no longer waiting"
        try:
            _call("answerCallbackQuery", callback_query_id=callback["id"], text=note)
            where = _sent.pop(int(raw_id), None) if decided else None
            if where:
                _call("editMessageReplyMarkup", chat_id=where[0], message_id=where[1], reply_markup={"inline_keyboard": []})
                send(where[0], "✅ Allowed - doing it now." if verb == "ok" else "❌ Denied - nothing was done.")
        except (TelegramError, httpx.HTTPError):
            pass
        return

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat.get("type") != "private":
        return  # groups and channels never command anything
    text = (message.get("text") or "").strip()
    sender = message.get("from") or {}
    who = f"@{sender['username']}" if sender.get("username") else sender.get("first_name")
    if text.startswith("/link"):
        code = text.removeprefix("/link").strip()
        linked = _link(chat_id, code, who) if code else None
        send(chat_id, "Linked. This chat now talks to your Custodian - ask me anything, or tell me what to do on your PC."
             if linked else "That code isn't right or has expired. Make a new one in Custodian → Tools & Data → Telegram.")
        return
    user_id = user_for(chat_id)
    if user_id is None:
        send(chat_id, "This is a private Custodian. If it's yours, open Tools & Data → Telegram in the app and send "
                      "the /link code it shows.")
        return
    if text in ("/start", "/help"):
        send(chat_id, "Ask me anything, or tell me what to do on your PC (“lock my PC”, “play lofi on Spotify”). "
                      "Anything risky asks you here first. /new starts a fresh conversation.")
        return
    if text == "/new":
        _conversations.pop(chat_id, None)
        send(chat_id, "Started a new conversation.")
        return
    if not text:
        try:
            text = _transcribe(message, user_id) or ""
        except Exception as exc:  # noqa: BLE001 - voice notes are a bonus
            log.info("couldn't transcribe a Telegram voice note: %s", exc)
        if not text:
            send(chat_id, "I can read text messages, and voice notes when speech-to-text is set up.")
            return
    threading.Thread(target=_chat_turn, args=(user_id, chat_id, text), daemon=True, name="telegram-turn").start()


def _poll() -> None:
    offset, delay = 0, 2.0
    while not _stop.is_set():
        try:
            updates = _call("getUpdates", timeout=25, offset=offset, allowed_updates=["message", "callback_query"]) or []
            delay = 2.0
        except (TelegramError, httpx.HTTPError, ValueError) as exc:
            log.warning("Telegram polling: %s", exc)
            _stop.wait(delay)
            delay = min(delay * 2, 120)
            continue
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            try:
                handle_update(update)
            except Exception:  # noqa: BLE001 - one bad update never stops the bot
                log.exception("a Telegram update failed")


def start(*, poll: bool = True) -> bool:
    """Send approval buttons for turns from Telegram, and - with TELEGRAM_BOT_TOKEN set and `poll` -
    start polling for messages (main.py: only in the process that runs the scheduler)."""
    global _thread
    if not approvals.listening(_ask):  # approvals.reset() forgets listeners (tests)
        approvals.on_request(_ask)
    if not poll or not configured() or (_thread is not None and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_poll, daemon=True, name="telegram")
    _thread.start()
    log.info("Telegram bot started")
    return True


def stop() -> None:
    _stop.set()


def reset() -> None:
    """Forget codes, chats and sent approvals (tests)."""
    global _bot_name
    with _lock:
        _codes.clear()
        _wrong.clear()
    _conversations.clear()
    _sent.clear()
    _bot_name = None
