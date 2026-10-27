"""Who a message or call goes to must be someone the user named.

A small model, asked "message him hi" or "whatsapp them that I'm late", sent to a chat called "Him", or
picked a name from somewhere else ("Tanishka") - the PC's Allow-once dialog would catch it, but nothing
should get that far. Before pc_send_message, pc_call or pc_open_chat runs, the contact is checked against
what was said in this conversation (and what the tools showed this turn): a pronoun, or a name nobody
said, is refused, and the model is told to ask who.

Allowed without the name in the text: a request in Hindi or Marathi (names are written in Latin letters
for WhatsApp), a relationship said ("text my wife": Custodian may know who that is), and a number given.
"""

from __future__ import annotations

import difflib
import re

GUARDED = {"pc_send_message": "message", "pc_call": "call", "pc_open_chat": "open the chat of"}
PRONOUNS = frozenset("him her them he she they it his hers their someone somebody anyone anybody everyone everybody all "
                     "person contact friend friends people guy girl".split())
RELATIONS = frozenset("mum mom mummy mother maa ma dad daddy papa father wife husband hubby brother bro sister sis son "
                      "daughter boss manager grandma grandpa granny nani nana dadi dada uncle aunt aunty chacha mama "
                      "girlfriend boyfriend gf bf partner fiance fiancee colleague teacher sir madam".split())
FILLER = frozenset("on in to the my chat with whatsapp video voice call".split())
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def check(function: str, args: dict, heard: list[str]) -> str | None:
    """Why this call must not run (to tell the model), or None when the recipient was named."""
    verb = GUARDED.get(function)
    if verb is None or not isinstance(args, dict):
        return None
    contact = str(args.get("contact") or "")
    named = [w for w in _words(contact) if w not in FILLER]
    if not named or all(w in PRONOUNS for w in named):
        return (f"{contact!r} isn't a name. Don't {verb} anyone: ask the user who they mean, "
                "in one short question.")
    text = " ".join(heard)
    if DEVANAGARI.search(text):
        return None  # said in Hindi or Marathi: the name is spelled in Latin letters here
    said = set(_words(text))
    digits = re.sub(r"\D", "", contact)
    if len(digits) >= 7 and digits in re.sub(r"\D", "", text):
        return None
    if said & RELATIONS:
        return None
    for word in named:
        if word in said or any(len(word) >= 3 and difflib.SequenceMatcher(None, word, s).ratio() >= 0.75 for s in said):
            return None
    return (f"The user never named {contact!r} in this conversation. Don't {verb} anyone they didn't name: "
            "ask who they mean, in one short question.")
