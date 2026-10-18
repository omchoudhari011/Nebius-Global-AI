"""Deeper memory: who and what your memories are about, and facts that change.

1. The graph. Each memory is read for the people, places, projects, organisations and pets it
   names, and how they relate - "Their sister Meera lives in Pune" gives Meera (your sister) and
   Pune (where Meera lives). They're stored as entities and links (schema.sql), encrypted like the
   memories. Then a question that names one - or says "my sister" - also brings the memories about
   it into the reply's context, and those about whatever it's linked to: "what does my sister do?"
   finds "Meera works at Infosys" though it never says "sister".

2. Facts that change. A new memory that gives a new value for something a memory already says -
   where someone lives, where they work, a favourite, being married - replaces it: the older one
   is kept as history (valid_until, superseded_by) instead of contradicting the new one. The
   prompt then shows it as "no longer true since <date>", so "where do I live?" gets the new
   answer and "where did I live before?" the old one. Deleting the new memory makes the old one
   current again (a trigger).

Both use plain rules on the memory's own words - names must appear in it, nothing is guessed - so
they run instantly after each save (process_pending), and once a minute for anything missed.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db
from .crypto import decrypt, encrypt

log = logging.getLogger(__name__)

SELF = "(you)"  # the user, as an entity: relations to them start here
RELATIONS = ("best friend", "elder sister", "younger sister", "elder brother", "younger brother", "sister", "brother", "mother",
             "mom", "mum", "father", "dad", "wife", "husband", "son", "daughter", "friend", "boss", "manager", "colleague",
             "coworker", "teammate", "partner", "girlfriend", "boyfriend", "fiance", "fiancé", "fiancee", "landlord",
             "landlady", "neighbour", "neighbor", "cousin", "uncle", "aunt", "grandmother", "grandma", "grandfather",
             "grandpa", "niece", "nephew", "roommate", "flatmate", "doctor", "dentist", "teacher", "mentor", "client", "baby")
PETS = ("dog", "cat", "puppy", "kitten", "parrot", "bird", "fish", "rabbit", "hamster", "pet")
# Names must be capitalised; every other word in these patterns matches in any case: (?i:...).
_REL = "(?i:" + "|".join(sorted((re.escape(r) for r in RELATIONS), key=len, reverse=True)) + ")"
_NAME = r"[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,2}"
_OWNER = r"(?i:their|my|his|her|the\s+user's)"
_IS = r"(?i:is)"
NOT_NAMES = frozenset({"They", "Their", "Them", "He", "She", "His", "Her", "The", "A", "An", "I", "My", "It", "Is", "On", "In",
                       "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January", "February",
                       "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"})

_PERSON_PATTERNS = (
    re.compile(rf"\b{_OWNER}\s+({_REL})\s+(?:{_IS}\s+)?(?:(?i:called|named)\s+)?(?:,\s*)?({_NAME})"),
    re.compile(rf"\b{_OWNER}\s+({_REL})'s\s+(?i:name\s+is)\s+({_NAME})"),
    re.compile(rf"\b({_NAME})\s+{_IS}\s+{_OWNER}\s+({_REL})\b"),
)
_PET = re.compile(rf"\b{_OWNER}\s+((?i:{'|'.join(PETS)}))\s+(?:{_IS}\s+)?(?i:called|named)\s+({_NAME})")
_PLACE = re.compile(rf"\b(?i:lives?|living|moved|relocated|shifted|stays?|staying|based|born|grew\s+up|from|visiting|visits?|trip)"
                    rf"\s+(?:(?:(?i:from)\s+{_NAME}\s+)?(?i:in|to|at|from|near)\s+)?({_NAME})")
_ORG = re.compile(rf"\b(?i:works?|working|worked|joined|employed|studies|studying|studied|interning|interns?)\s+(?i:at|for|with|in)\s+({_NAME})")
_PROJECT = re.compile(rf"\b(?i:project|app|startup|product|side\s+project)\s+(?i:called|named)\s+({_NAME})|\b(?i:working\s+on)\s+({_NAME})")


@dataclass(frozen=True)
class Found:
    name: str
    kind: str
    relation: str | None  # how it relates to the user, when the memory says ("sister", "lives in")
    owner: str | None = None  # the person it belongs to instead of the user ("Meera" lives in Pune)


def _clean(name: str) -> str | None:
    words = [w for w in name.replace("’", "'").split() if w not in NOT_NAMES]
    while words and words[-1].lower() in ("and", "with", "since", "for", "in", "at", "on"):
        words.pop()
    name = " ".join(words).strip(" .,'")
    return name if name and name[0].isupper() else None


def extract(text: str, known_people: tuple[str, ...] = ()) -> list[Found]:
    """The entities one memory names, in its own words (a name must appear in it). `known_people`
    are people already on the map, found by name too: "Meera works at Infosys" is about Meera."""
    found: dict[str, Found] = {}
    people: list[tuple[int, str]] = []  # (position, name) of the people named, to tell whose place it is

    def add(item: Found) -> None:
        if item.name.lower() not in {k.lower() for k in found}:
            found[item.name] = item

    for pattern in _PERSON_PATTERNS:
        for match in pattern.finditer(text):
            relation, name = (match.group(1), match.group(2)) if pattern is not _PERSON_PATTERNS[2] else (match.group(2), match.group(1))
            name = _clean(name)
            if name:
                add(Found(name, "person", relation.lower()))
                people.append((match.start(), name))
    for name in known_people:
        for match in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            add(Found(match.group(0), "person", None))
            people.append((match.start(), match.group(0)))
    people.sort()
    for match in _PET.finditer(text):
        if name := _clean(match.group(2)):
            add(Found(name, "pet", f"{match.group(1).lower()}"))
    for pattern, kind, verb in ((_PLACE, "place", None), (_ORG, "organization", None)):
        for match in pattern.finditer(text):
            name = _clean(match.group(1))
            if not name or name in found:
                continue
            said = match.group(0).split()[0].lower()
            relation = ("works at" if kind == "organization" and said.startswith(("work", "join", "employ")) else
                        "studies at" if kind == "organization" else
                        "is from" if said in ("from", "born") else "visits" if said.startswith(("visit", "trip")) else "lives in")
            owner = next((who for position, who in reversed(people) if position < match.start()), None)
            add(Found(name, kind, relation, owner))
    for match in _PROJECT.finditer(text):
        if name := _clean(match.group(1) or match.group(2) or ""):
            add(Found(name, "project", "works on"))
    return list(found.values())


# Facts that change ----------------------------------------------------------------------------------

_ATTRIBUTES: tuple[tuple[str, re.Pattern], ...] = (
    ("home", re.compile(rf"\b(?i:moved\s+from)\s+{_NAME}\s+(?i:to)\s+({_NAME})|\b(?i:lives?|living|moved|relocated|shifted|stays?"
                        rf"|staying|based)\s+(?i:in|to|at)\s+({_NAME})")),
    ("work", re.compile(rf"\b(?i:works?|working|joined|employed|new\s+job)\s+(?i:at|for|with)\s+({_NAME})")),
    ("favourite", re.compile(r"\bfavou?rite\s+([a-z][a-z ]{1,30}?)\s+is\s+([^.;]+)", re.I)),
    ("relationship", re.compile(r"\b(?:is|got|are|became)\s+(married|engaged|divorced|single|separated)\b", re.I)),
    ("diet", re.compile(r"\b(?:is|became|turned|went)\s+(vegetarian|vegan|non-vegetarian|pescatarian|eggetarian)\b", re.I)),
    ("car", re.compile(r"\b(?:their|my)\s+car\s+is\s+([^.;]+)|\bdrives?\s+an?\s+([^.;]+)", re.I)),
    ("phone", re.compile(r"\b(?:phone|mobile)(?:\s+number)?\s+is\s+([+\d][\d\s-]{6,})", re.I)),
)


def attribute(text: str, known_people: tuple[str, ...] = ()) -> tuple[str, str, str] | None:
    """(attribute, whose, value) a memory states - ("home", "self", "mumbai") - or None. A plan
    ("is moving to Pune next month") isn't a change yet, so it's not one."""
    if re.search(r"\b(?:will|going\s+to|plans?\s+to|planning|moving\s+to|next\s+(?:week|month|year))\b", text, re.I):
        return None
    for name, pattern in _ATTRIBUTES:
        match = pattern.search(text)
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        if name == "favourite":
            name, value = f"favourite {groups[0].strip().lower()}", groups[1]
        else:
            value = groups[-1]
        person = next((f.name for f in extract(text[: match.start()], known_people) if f.kind == "person"), None)
        return name, (person or "self").lower(), " ".join(value.lower().split()).strip(" .")
    return None


# Storing --------------------------------------------------------------------------------------------

def _entities(conn, user_id: int) -> dict[str, tuple[int, str]]:  # noqa: ANN001
    """{lowercased name: (id, kind)} of the user's entities."""
    return {decrypt(r["name"]).lower(): (r["id"], r["kind"]) for r in conn.execute(
        "SELECT id, name, kind FROM entities WHERE user_id = ?", (user_id,))}


def _entity(conn, user_id: int, known: dict, name: str, kind: str) -> int:  # noqa: ANN001
    key = name.lower()
    if key in known:
        return known[key][0]
    entity_id = conn.execute("INSERT INTO entities (user_id, kind, name) VALUES (?, ?, ?)",
                             (user_id, kind, encrypt(name))).lastrowid
    known[key] = (entity_id, kind)
    return entity_id


def _link(conn, source: int, target: int, relation: str, memory_id: int) -> None:  # noqa: ANN001
    for row in conn.execute("SELECT relation FROM entity_links WHERE source_id = ? AND target_id = ?", (source, target)):
        if decrypt(row["relation"]) == relation:
            return
    conn.execute("INSERT INTO entity_links (source_id, target_id, relation, memory_id) VALUES (?, ?, ?, ?)",
                 (source, target, encrypt(relation), memory_id))


def link_memory(conn, user_id: int, memory_id: int, text: str) -> int:  # noqa: ANN001
    """Map one memory's entities and links. Returns how many entities it names."""
    known = _entities(conn, user_id)
    names = tuple(decrypt(r["name"]) for r in conn.execute(
        "SELECT name FROM entities WHERE user_id = ? AND kind = 'person'", (user_id,)))
    items = extract(text, tuple(n for n in names if n != SELF))
    me = _entity(conn, user_id, known, SELF, "person")
    for item in items:
        entity_id = _entity(conn, user_id, known, item.name, item.kind)
        conn.execute("INSERT OR IGNORE INTO memory_entities (memory_id, entity_id) VALUES (?, ?)", (memory_id, entity_id))
        if item.relation:
            owner = _entity(conn, user_id, known, item.owner, "person") if item.owner else me
            _link(conn, owner, entity_id, item.relation, memory_id)
    return len(items)


def supersede(conn, user_id: int, memory_id: int, text: str) -> list[int]:  # noqa: ANN001
    """Mark older memories this one replaces (same attribute, same person, another value).
    Returns their ids."""
    people = tuple(n for n in (decrypt(r["name"]) for r in conn.execute(
        "SELECT name FROM entities WHERE user_id = ? AND kind = 'person'", (user_id,))) if n != SELF)
    said = attribute(text, people)
    if said is None:
        return []
    name, whose, value = said
    replaced = []
    for row in conn.execute(
        """
        SELECT id, content FROM memories WHERE user_id = ? AND id < ? AND superseded_by IS NULL
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
        (user_id, memory_id),
    ).fetchall():
        old = attribute(decrypt(row["content"]), people)
        if old is not None and old[0] == name and old[1] == whose and old[2] != value:
            replaced.append(row["id"])
    if replaced:
        stamp = datetime.now(timezone.utc).strftime(db.DB_TIME_FORMAT)
        marks = ",".join("?" * len(replaced))
        conn.execute(f"UPDATE memories SET superseded_by = ?, valid_until = ? WHERE id IN ({marks})",
                     (memory_id, stamp, *replaced))
        log.info("memory %s replaces %s (%s)", memory_id, replaced, name)
    return replaced


def mark_replaced(user_id: int, new_id: int, old_id: int) -> bool:
    """The model said a new memory replaces an old one (the remember tool's `replaces`)."""
    with db.connect() as conn:
        return conn.execute(
            """
            UPDATE memories SET superseded_by = ?, valid_until = datetime('now')
            WHERE id = ? AND user_id = ? AND id != ? AND superseded_by IS NULL
              AND EXISTS (SELECT 1 FROM memories WHERE id = ? AND user_id = ?)
            """,
            (new_id, old_id, user_id, new_id, new_id, user_id),
        ).rowcount > 0


_running = threading.Lock()


def process_pending(limit: int = 50) -> int:
    """Map and check every memory not processed yet, oldest first. Returns how many."""
    if not _running.acquire(blocking=False):
        return 0
    try:
        with db.connect() as conn:
            rows = conn.execute("SELECT id, user_id, content FROM memories WHERE linked = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()
            for row in rows:
                text = decrypt(row["content"])
                link_memory(conn, row["user_id"], row["id"], text)
                supersede(conn, row["user_id"], row["id"], text)
                conn.execute("UPDATE memories SET linked = 1 WHERE id = ?", (row["id"],))
        _cache.clear()
        return len(rows)
    finally:
        _running.release()


def kick() -> None:
    """Process new memories in the background now (after a turn that saved some)."""
    threading.Thread(target=process_pending, daemon=True, name="memory-graph").start()


def forget_entities(user_id: int) -> None:
    """Drop entities no memory mentions any more (after deletes)."""
    with db.connect() as conn:
        conn.execute(
            """
            DELETE FROM entities WHERE user_id = ? AND id NOT IN (SELECT entity_id FROM memory_entities)
              AND id NOT IN (SELECT source_id FROM entity_links) AND id NOT IN (SELECT target_id FROM entity_links)
            """,
            (user_id,),
        )
    _cache.clear()


# Using it -------------------------------------------------------------------------------------------

_cache: dict[int, list[dict]] = {}


def graph(user_id: int) -> list[dict]:
    """Every entity with its kind, how it relates to you, its links and the memories naming it."""
    if user_id in _cache:
        return _cache[user_id]
    with db.connect() as conn:
        names = {r["id"]: (decrypt(r["name"]), r["kind"]) for r in conn.execute(
            "SELECT id, name, kind FROM entities WHERE user_id = ?", (user_id,))}
        links = [(r["source_id"], r["target_id"], decrypt(r["relation"])) for r in conn.execute(
            "SELECT l.* FROM entity_links l JOIN entities e ON e.id = l.source_id WHERE e.user_id = ?", (user_id,))]
        mentions: dict[int, list[int]] = {}
        for r in conn.execute("SELECT me.entity_id, me.memory_id FROM memory_entities me JOIN entities e ON e.id = me.entity_id "
                              "WHERE e.user_id = ? ORDER BY me.memory_id DESC", (user_id,)):
            mentions.setdefault(r["entity_id"], []).append(r["memory_id"])
    out = []
    for entity_id, (name, kind) in names.items():
        if name == SELF:
            continue
        out.append({
            "id": entity_id, "name": name, "kind": kind,
            "relation": next((rel for s, t, rel in links if t == entity_id and names.get(s, ("",))[0] == SELF), None),
            "links": [{"relation": rel, "to": names[t][0]} for s, t, rel in links if s == entity_id and t in names]
            + [{"relation": f"{rel} of", "to": names[s][0]} for s, t, rel in links
               if t == entity_id and s in names and names[s][0] != SELF],
            "memories": mentions.get(entity_id, []),
        })
    out.sort(key=lambda e: (-len(e["memories"]), e["name"].lower()))
    _cache[user_id] = out
    return out


def related_memory_ids(user_id: int, message: str, limit: int = 8) -> list[int]:
    """Memories about the people, places and things a message names - by name, or by how they
    relate to the user ("my sister") - and about what those are linked to. Newest first."""
    entities = graph(user_id)
    if not entities:
        return []
    text = " " + " ".join(re.findall(r"[\w'’-]+", message.lower())) + " "
    hits = [e for e in entities if f" {e['name'].lower()} " in text
            or (e["relation"] and re.search(rf"\b(?:my|our)\s+{re.escape(e['relation'])}\b", text))]
    by_name = {e["name"]: e for e in entities}
    found: list[int] = []
    for entity in hits:
        for memory_id in entity["memories"]:
            if memory_id not in found:
                found.append(memory_id)
        for link in entity["links"]:  # one step out: Meera -> Pune, Infosys
            for memory_id in by_name.get(link["to"], {}).get("memories", [])[:3]:
                if memory_id not in found:
                    found.append(memory_id)
    return found[:limit]
