"""What Custodian treats as a secret: passwords and other credentials, card and bank account
numbers, government ID numbers (Aadhaar, PAN, passport, SSN) and API keys or tokens.

Secrets never become memories (memory.write refuses them, whoever asks) and are never
recalled from earlier chats (history_search skips those messages), so they don't follow the
user into later prompts - or, with a cloud model, get sent out again. When the model runs on
the internet, redact() also replaces them in everything sent to it (app/privacy.py), so a
password typed into the current message stays on this server too. The checks lean
towards blocking: "I changed my password" is kept out too. Numbers that are only sensitive
in context (a 12-digit Aadhaar, a bank account) need their keyword or their usual format, so
phone numbers, postal PIN codes and dates stay rememberable.
"""

from __future__ import annotations

import re

_KINDS = {
    "credential": "a password or other login secret",
    "card": "a card number",
    "account": "a bank account number",
    "aadhaar": "an Aadhaar number",
    "pan": "a PAN",
    "passport": "a passport number",
    "ssn": "a Social Security number",
    "token": "an API key or access token",
}

_PATTERNS = {
    "credential": (
        r"\b(?:password|passwd|passcode|pass\s?phrase|one[- ]time\s+(?:code|password)|otp|cvv|cvc"
        r"|security\s+code|api\s*key|secret\s+key|private\s+key|access\s+token|auth(?:entication)?\s+token"
        r"|bearer\s+token|seed\s+phrase|recovery\s+phrase|m-?pin|(?:atm|card|debit|credit|upi|bank|phone|sim)\s+pin"
        r"|pin\s*(?:number|no\.?)?\s*(?:is|:|=)\s*\d{3,})\b"
    ),
    "card": r"\b(?:\d[ -]?){12,18}\d\b",  # before Aadhaar: a card's first 12 digits look like one
    "aadhaar": r"\b(?:aadhaa?r|uidai)\b\D{0,25}\d{4}[ -]?\d{4}[ -]?\d{4}\b|\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b",
    "account": r"\b(?:account|acct|a/c)\s*(?:no\.?|number|num|#)?\s*(?:is|:|=|-)?\s*\d[\d -]{6,}\d\b",
    "pan": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    "passport": r"\bpassport\s*(?:no\.?|number|num|#)?\s*(?:is|:|=|-)?\s*(?=[A-Z]*\d)[A-Z0-9]{6,9}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "token": (
        r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b"
        r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\beyJ[\w-]{10,}\.[\w-]{10,}\."
    ),
}
_SECRET = re.compile("|".join(f"(?P<{kind}>{pattern})" for kind, pattern in _PATTERNS.items()), re.IGNORECASE)


def find(text: str) -> str | None:
    """What kind of secret `text` holds ("a card number"), or None if it looks safe."""
    match = _SECRET.search(text)
    return _KINDS[match.lastgroup] if match and match.lastgroup else None


def looks_secret(text: str) -> bool:
    return find(text) is not None


# Redaction: find() only has to notice a secret, so for credentials it matches the keyword
# ("password"). Withholding one from the model means removing the value that follows it
# ("password: hunter2" -> "password: [secret withheld]"), so redact() has its own patterns.
# A quoted value (also JSON-escaped, \"...\") runs to its closing quote; a bare one is a single
# word. Neither crosses a quote or backslash, so redacting a tool result keeps its JSON valid,
# and a linking word ("is") is never taken for the value, or `password is x` would lose "is".
_VALUE = (
    r"(?P<quote>\\?[\"'“‘])(?!\[)[^\"'“”‘’\\\n]{1,120}"
    r"|(?![:=])(?!(?:is|was|are|for|to|of|the|my)\b)[^\s,;\"'“”‘’<>()\[\]{}\\]+"
)
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_PHRASE = re.compile(r"(?P<label>\b(?:seed|recovery|pass)\s*phrase\b(?:\s+(?:is|was))?\s*[:=-]?\s*)(?P<value>(?![\s\[:=-])(?!(?:is|was)\b)[^.\n\"\\]+)", re.I)
_CREDENTIAL = re.compile(
    r"(?P<label>\b(?:password|passwd|passcode|pin(?:\s*(?:number|no\.?|code))?|m-?pin|otp|one[- ]time\s+(?:code|password)"
    r"|cvv|cvc|security\s+code|api\s*key|secret\s+key|access\s+token|auth(?:entication)?\s+token|bearer\s+token)\b"
    r"(?:\s+(?:for|to)\s+[\w.@-]+\b(?:\s+(?!(?:is|was)\b)[\w.@-]+\b)?)?(?:\s+(?:is|was|=|-))?\s*[:=]?\s*)"
    rf"(?P<value>{_VALUE})",
    re.I,
)
# The kinds that find() matches together with their value: the whole match goes.
_WITH_VALUE = {
    "card": "card number", "aadhaar": "Aadhaar number", "account": "account number", "pan": "PAN",
    "passport": "passport number", "ssn": "Social Security number", "token": "access token",
}
_WHOLE = [(label, re.compile(_PATTERNS[kind], re.IGNORECASE)) for kind, label in _WITH_VALUE.items()]


def redact(text: str) -> tuple[str, int]:
    """`text` with every secret replaced by a placeholder such as "[card number withheld]",
    and how many were replaced. Used on what is sent to a model on the internet."""
    count = 0

    def sub(pattern: re.Pattern, replacement) -> None:  # type: ignore[type-arg, no-untyped-def]
        nonlocal text, count
        text, n = pattern.subn(replacement, text)
        count += n

    sub(_PRIVATE_KEY_BLOCK, "[private key withheld]")
    sub(_PHRASE, lambda m: m.group("label") + "[phrase withheld]")
    sub(_CREDENTIAL, lambda m: m.group("label") + (m.group("quote") or "") + "[secret withheld]")
    for label, pattern in _WHOLE:
        sub(pattern, f"[{label} withheld]")
    return text, count
