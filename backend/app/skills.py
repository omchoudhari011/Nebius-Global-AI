"""Versioned prompt templates ("skills").

Each skill is a file in backend/skills/ with a small front matter block:

    ---
    name: calendar
    description: Reads and adds events on the local calendar.
    keywords: calendar, meeting, event
    tools: calendar:read, calendar:write
    ---
    Prompt text with $now, $timezone, $memories and any task params as $name.

On startup sync_from_files() compares each file with the newest saved version of that
skill. If the prompt or tool list changed it inserts a new version (rows are immutable;
the skills_immutable trigger blocks edits), so a task pinned to an older version keeps
running exactly what it ran before. load_skill(name) returns the highest active version.

The orchestrator picks a skill by keyword match on the message - no second model call.
"""

from __future__ import annotations

import re
import sqlite3
import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import db
from .config import BACKEND_DIR

SKILLS_DIR = BACKEND_DIR / "skills"
DEFAULT_SKILL = "general"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SkillFileError(ValueError):
    """A file in backend/skills/ is malformed or names an unknown tool scope."""


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    keywords: tuple[str, ...]
    tools: tuple[tuple[str, str], ...]  # (tool name, scope)
    prompt: str
    path: Path


@dataclass(frozen=True)
class Skill:
    id: int
    name: str
    version: int
    prompt_template: str
    active: bool
    tools: frozenset[tuple[str, str]]  # (tool name, scope) this version may use

    def render(self, variables: dict[str, str]) -> str:
        # $name placeholders: braces and other punctuation in prompts stay literal.
        return string.Template(self.prompt_template).safe_substitute(variables)

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version}


def parse_skill_file(path: Path) -> SkillDefinition:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise SkillFileError(f"{path.name}: must start with a --- front matter block")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise SkillFileError(f"{path.name}: front matter block isn't closed with ---")
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise SkillFileError(f"{path.name}: expected 'key: value', got {line!r}")
        meta[key.strip().lower()] = value.strip()
    prompt = text[end + 5:].strip()

    name = meta.get("name", "")
    if not _NAME_RE.match(name):
        raise SkillFileError(f"{path.name}: name must be lowercase letters, digits and dashes")
    if not prompt:
        raise SkillFileError(f"{path.name}: the prompt template is empty")

    tools: list[tuple[str, str]] = []
    for item in filter(None, (t.strip() for t in meta.get("tools", "").split(","))):
        tool, sep, scope = item.partition(":")
        if not sep or not tool or not scope:
            raise SkillFileError(f"{path.name}: tools must be written tool:scope, got {item!r}")
        tools.append((tool.strip(), scope.strip()))

    keywords = tuple(k.strip().lower() for k in meta.get("keywords", "").split(",") if k.strip())
    return SkillDefinition(
        name=name,
        description=meta.get("description", ""),
        keywords=keywords,
        tools=tuple(sorted(set(tools))),
        prompt=prompt,
        path=path,
    )


@lru_cache(maxsize=1)
def definitions() -> dict[str, SkillDefinition]:
    defs: dict[str, SkillDefinition] = {}
    for path in sorted(SKILLS_DIR.glob("*.md")):
        skill = parse_skill_file(path)
        if skill.name in defs:
            raise SkillFileError(f"two skill files are named {skill.name!r}")
        defs[skill.name] = skill
    return defs


def _tools_of(conn: sqlite3.Connection, skill_id: int) -> frozenset[tuple[str, str]]:
    rows = conn.execute(
        "SELECT t.name, st.scope FROM skill_tools st JOIN tools t ON t.id = st.tool_id WHERE st.skill_id = ?",
        (skill_id,),
    ).fetchall()
    return frozenset((r["name"], r["scope"]) for r in rows)


def _from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Skill:
    return Skill(
        id=row["id"],
        name=row["name"],
        version=row["version"],
        prompt_template=row["prompt_template"],
        active=bool(row["active"]),
        tools=_tools_of(conn, row["id"]),
    )


def sync_from_files(*, conn: sqlite3.Connection | None = None) -> list[tuple[str, int]]:
    """Insert a new version for every skill file that differs from its newest saved version."""
    added: list[tuple[str, int]] = []
    with db.use(conn) as c:
        for d in definitions().values():
            latest = c.execute(
                "SELECT * FROM skills WHERE name = ? ORDER BY version DESC LIMIT 1", (d.name,)
            ).fetchone()
            if latest is not None:
                if latest["prompt_template"] == d.prompt and _tools_of(c, latest["id"]) == frozenset(d.tools):
                    continue
            version = latest["version"] + 1 if latest is not None else 1
            skill_id = c.execute(
                "INSERT INTO skills (name, version, prompt_template) VALUES (?, ?, ?)",
                (d.name, version, d.prompt),
            ).lastrowid
            for tool_name, scope in d.tools:
                scope_row = c.execute(
                    "SELECT s.tool_id FROM tool_scopes s JOIN tools t ON t.id = s.tool_id WHERE t.name = ? AND s.scope = ?",
                    (tool_name, scope),
                ).fetchone()
                if scope_row is None:
                    raise SkillFileError(f"{d.path.name}: unknown tool scope {tool_name}:{scope}")
                c.execute(
                    "INSERT INTO skill_tools (skill_id, tool_id, scope) VALUES (?, ?, ?)",
                    (skill_id, scope_row["tool_id"], scope),
                )
            added.append((d.name, version))
    return added


def load_skill(name: str, *, conn: sqlite3.Connection | None = None) -> Skill | None:
    """The highest active version of a skill, or None."""
    with db.use(conn) as c:
        row = c.execute(
            "SELECT * FROM skills WHERE name = ? AND active = 1 ORDER BY version DESC LIMIT 1", (name,)
        ).fetchone()
        return _from_row(c, row) if row else None


def get_skill(skill_id: int, *, conn: sqlite3.Connection | None = None) -> Skill | None:
    """One exact version, active or not (tasks pin a version)."""
    with db.use(conn) as c:
        row = c.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return _from_row(c, row) if row else None


def list_skills(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """The newest active version of every skill."""
    with db.use(conn) as c:
        rows = c.execute(
            """
            SELECT s.* FROM skills s
            WHERE s.active = 1 AND s.version = (
              SELECT MAX(version) FROM skills x WHERE x.name = s.name AND x.active = 1
            )
            ORDER BY s.name
            """
        ).fetchall()
        result = []
        for row in rows:
            skill = _from_row(c, row)
            d = definitions().get(skill.name)
            result.append(
                {
                    **skill.summary(),
                    "description": d.description if d else "",
                    "tools": sorted(f"{t}:{s}" for t, s in skill.tools),
                    "created_at": db.iso(row["created_at"]),
                }
            )
        return result


# Words that make a request about your Custodian records rather than the PC.
PERSONAL_DATA = ("calendar", "meeting", "meetings", "event", "events", "appointment", "appointments", "schedule", "agenda",
                 "note", "notes", "reminder", "reminders", "email", "emails", "mail", "inbox", "message from", "uploaded",
                 "upload", "my day", "my week", "voice note")
# Requests that start like an order for the PC count one extra towards the PC skill.
PC_COMMAND_STARTS = tuple(f" {w} " for w in (
    "open", "launch", "play", "fire up", "start up", "take me", "go to", "bring up", "pull up", "close", "quit", "kill",
    "whatsapp", "text", "message", "send a whatsapp", "send a message", "mute", "unmute", "pause", "resume", "skip",
    "lock", "put on", "switch to", "minimize", "turn up", "turn down", "dim", "brighten", "organize", "organise",
    "i want to listen", "i want to watch", "i want to see", "show me", "click", "press", "type", "i need"))
COMMAND_VERBS = {"open", "play", "launch", "start", "close", "quit", "take", "go", "show", "search", "set", "turn",
                 "mute", "pause", "skip", "lock", "type", "write", "find", "organize", "organise", "click", "fill",
                 "switch", "put", "make", "copy", "move", "delete", "save", "sort", "clean", "look", "check", "hit",
                 "press", "dim", "create", "kill", "stop", "resume", "connect", "minimize", "draft", "whatsapp", "text",
                 "message", "bring", "fire", "pull", "listen", "watch"}


def pick_skill_name(message: str) -> str:
    """The skill whose keywords match the message best; DEFAULT_SKILL when none or a tie."""
    text = " " + " ".join(re.findall(r"\w+", message.lower())) + " "
    scores: dict[str, int] = {}
    for d in definitions().values():
        hits = sum(1 for k in d.keywords if f" {k} " in text)
        if hits:
            scores[d.name] = hits
    personal = any(f" {w} " in text for w in PERSONAL_DATA)  # about Custodian's own records, not the PC
    if text.startswith(PC_COMMAND_STARTS) and not personal:
        scores["pc"] = scores.get("pc", 0) + 1  # "fire up calculator", "put on Heeriye": an order for the PC
    if not scores:
        return DEFAULT_SKILL
    best = max(scores.values())
    winners = [name for name, score in scores.items() if score == best]
    if len(winners) > 1 and "pc" in winners:
        others = [w for w in winners if w != "pc"]
        if personal and len(others) == 1:
            return others[0]  # "show me my calendar", "open the lease I uploaded"
        if not personal and text.split()[0] in COMMAND_VERBS:
            return "pc"  # "search amazon for earbuds", "whatsapp Sneha: call me when you're free"
    return winners[0] if len(winners) == 1 else DEFAULT_SKILL
