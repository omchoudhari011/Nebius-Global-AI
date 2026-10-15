"""Which of the user's past messages are worth recalling in a later chat: statements, not
questions ("what's my name?"), small talk ("thanks, that helps") or secrets. Questions and
small talk hold no facts and would crowd out the messages that do. Keyword search
(app/history_search.py) and search by meaning (app/vectors.py, which doesn't even embed the
rest) apply the same rule.
"""

from __future__ import annotations

import re

from . import sensitive

_QUESTION = re.compile(
    r"^\W*(?:(?:what|what's|whats|who|who's|whom|whose|where|where's|when|which|why|how)\b"
    r"|(?:do|does|did|is|are|am|was|were|can|could|will|would|should|have|has)\s+"
    r"(?:you|i|we|my|it|there|he|she|they|this|that)\b)",
    re.IGNORECASE,
)

_PLEASANTRIES = frozenset(
    """
    hi hello hey hiya there thanks thank thx ty you so much a lot ok okay k cool great nice awesome
    perfect fine good morning afternoon evening night day bye goodbye see later cheers that that's thats
    it helps helped all for the help now again everyone custodian lol haha yes yeah yep no nope sure
    """.split()
)


def looks_like_question(text: str) -> bool:
    return text.rstrip().endswith("?") or bool(_QUESTION.match(text))


def looks_like_small_talk(text: str) -> bool:
    """Nothing but pleasantries ("thanks, that helps", "good morning"): no facts to recall."""
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and all(word in _PLEASANTRIES for word in words)


def worth_recalling(text: str) -> bool:
    return not (looks_like_question(text) or looks_like_small_talk(text) or sensitive.looks_secret(text))
