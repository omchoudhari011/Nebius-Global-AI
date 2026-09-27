-- Custodian schema v2 — see docs/PLAN.md (Step 1: structure, Step 2: finalized alongside encryption).
-- Design notes and the ER diagram: SCHEMA.md.
--
-- Conventions:
--   * Timestamps are UTC TEXT from datetime('now'), so they sort and compare as strings.
--   * BLOB columns marked "Fernet" hold ciphertext from app/crypto.py, never plaintext.
--   * app/db.py turns on PRAGMA foreign_keys for every connection. SQLite ignores every
--     REFERENCES / ON DELETE below without it.
--   * app/db.init_db() runs this once on an empty database; user_version records which
--     schema the file holds.

PRAGMA journal_mode = WAL;               -- API requests and scheduler jobs write concurrently

BEGIN;

-- Identity ----------------------------------------------------------------------------

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Tools and permissions ---------------------------------------------------------------

CREATE TABLE tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  category TEXT,
  executor TEXT NOT NULL DEFAULT 'sandbox'
    CHECK (executor IN ('sandbox', 'openshell', 'nemoclaw')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))  -- 0 blocks the tool regardless of grants
);

-- The scopes each tool actually has. permissions, skill_tools and tool_calls reference this,
-- so a mistyped scope fails at INSERT instead of silently never matching the default-deny gate.
CREATE TABLE tool_scopes (
  tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
  scope TEXT NOT NULL,
  description TEXT,
  PRIMARY KEY (tool_id, scope)
);

-- Current state only: one row per (user, tool, scope), flipped by permissions.grant()/revoke().
-- History goes to permission_events via trigger, so app code can't skip writing it.
CREATE TABLE permissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tool_id INTEGER NOT NULL,
  scope TEXT NOT NULL,
  granted INTEGER NOT NULL DEFAULT 0 CHECK (granted IN (0, 1)),  -- default-deny; see app/permissions.py (Step 3)
  UNIQUE (user_id, tool_id, scope),
  FOREIGN KEY (tool_id, scope) REFERENCES tool_scopes(tool_id, scope)
);

CREATE TABLE permission_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
  granted INTEGER NOT NULL CHECK (granted IN (0, 1)),
  changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX permission_events_by_permission ON permission_events(permission_id, changed_at);

CREATE TRIGGER permissions_log_insert AFTER INSERT ON permissions
BEGIN
  INSERT INTO permission_events (permission_id, granted) VALUES (new.id, new.granted);
END;

CREATE TRIGGER permissions_log_update AFTER UPDATE OF granted ON permissions
WHEN old.granted IS NOT new.granted
BEGIN
  INSERT INTO permission_events (permission_id, granted) VALUES (new.id, new.granted);
END;

-- Skills ------------------------------------------------------------------------------

-- Skills are global (not per-user). Each row is one version and can't be changed after
-- insert: to edit a prompt, insert (name, version + 1). tasks point at a specific row, so a
-- new version never silently changes a scheduled job. load_skill(name) picks the highest
-- active version.
CREATE TABLE skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  prompt_template TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),  -- 0 rolls back a bad version without deleting it
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (name, version)
);

CREATE TRIGGER skills_immutable BEFORE UPDATE OF name, version, prompt_template ON skills
BEGIN
  SELECT RAISE(ABORT, 'skill versions are immutable; insert a new version instead');
END;

-- Allowlist of the tool scopes a skill may use. The sandbox denies any call outside the
-- current skill's list, so a scheduled task gets only what its skill needs, not every
-- permission the user has granted.
CREATE TABLE skill_tools (
  skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  tool_id INTEGER NOT NULL,
  scope TEXT NOT NULL,
  PRIMARY KEY (skill_id, tool_id, scope),
  FOREIGN KEY (tool_id, scope) REFERENCES tool_scopes(tool_id, scope)
);
CREATE INDEX skill_tools_by_scope ON skill_tools(tool_id, scope);

-- Conversations, messages, tool calls -------------------------------------------------

CREATE TABLE conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel TEXT NOT NULL DEFAULT 'chat' CHECK (channel IN ('chat', 'task')),  -- 'task' = opened by a task_run
  started_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX conversations_by_user ON conversations(user_id, started_at);

-- The API's `used` field is built from skill_id, message_memories and tool_calls rather than
-- stored as JSON, so deleting a memory can't leave a stale reference to it behind.
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
  content BLOB NOT NULL,                 -- Fernet ciphertext; chat text is as personal as the memories taken from it
  skill_id INTEGER REFERENCES skills(id),                             -- skill version behind an assistant turn
  tool_call_id INTEGER REFERENCES tool_calls(id) ON DELETE CASCADE,   -- the call a role='tool' row answers
  model TEXT,                            -- model backend id, assistant turns only
  latency_ms INTEGER,                    -- model time, assistant turns only; read by eval/latency_eval.py
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK ((role = 'tool') = (tool_call_id IS NOT NULL))
);
CREATE INDEX messages_by_conversation ON messages(conversation_id, created_at);
CREATE UNIQUE INDEX messages_by_tool_call ON messages(tool_call_id);  -- one result message per call

-- Every tool call the model proposes, including the ones that were stopped. This is the
-- audit log of what the agent did, and the source of the permission-block rate in eval/results.md.
CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,  -- assistant turn that proposed it
  tool_id INTEGER NOT NULL,
  scope TEXT NOT NULL,
  function TEXT NOT NULL,                -- e.g. 'create_event'
  args BLOB NOT NULL,                    -- Fernet ciphertext of the JSON arguments
  status TEXT NOT NULL CHECK (status IN (
    'rejected',                          -- args failed schema validation; never reached the gate
    'denied',                            -- blocked by the user's grants or the skill's allowlist
    'running', 'succeeded', 'failed', 'timed_out'
  )),
  result BLOB,                           -- Fernet ciphertext of the JSON result
  error TEXT,
  latency_ms INTEGER,                    -- tool time; read by eval/latency_eval.py
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  FOREIGN KEY (tool_id, scope) REFERENCES tool_scopes(tool_id, scope),
  CHECK ((status = 'running') = (finished_at IS NULL))
);
CREATE INDEX tool_calls_by_message ON tool_calls(message_id);
CREATE INDEX tool_calls_by_scope ON tool_calls(tool_id, scope, status);

-- Memory ------------------------------------------------------------------------------

CREATE TABLE memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL DEFAULT 'episodic' CHECK (type IN ('episodic', 'semantic', 'preference')),
  content BLOB NOT NULL,                 -- Fernet ciphertext, see app/crypto.py (Step 2)
  source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,  -- message it came from; NULL if seeded
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT                        -- NULL = keep; recall() skips expired rows, a scheduler job deletes them
);
CREATE INDEX memories_by_user ON memories(user_id, type);
CREATE INDEX memories_by_source ON memories(source_message_id);
CREATE INDEX memories_expiring ON memories(expires_at) WHERE expires_at IS NOT NULL;

CREATE TRIGGER memories_touch AFTER UPDATE OF type, content, expires_at ON memories
BEGIN
  UPDATE memories SET updated_at = datetime('now') WHERE id = new.id;
END;

-- Keyword recall index. Holds a plaintext copy of memory text for FTS5 MATCH queries —
-- a disclosed tradeoff, see the Step 2 notes in docs/PLAN.md.
-- rowid = memories.id: memory.write() inserts both rows in one transaction and must replace
-- the FTS row when content changes (only it has the plaintext). recall() joins back on
-- rowid to filter by user_id and skip expired rows. Deletes, including cascades from users,
-- are handled by the trigger below.
CREATE VIRTUAL TABLE memories_fts USING fts5(content, tokenize = 'porter unicode61');

CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories
BEGIN
  DELETE FROM memories_fts WHERE rowid = old.id;
END;

-- Memories recalled into the context of an assistant turn: the "memories" part of `used`.
CREATE TABLE message_memories (
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  PRIMARY KEY (message_id, memory_id)
);
CREATE INDEX message_memories_by_memory ON message_memories(memory_id);

-- Scheduled tasks ---------------------------------------------------------------------

-- This table is the source of truth. app/scheduler.py registers every active row with
-- APScheduler on startup and writes next_run_at back after each run. Last-run time and
-- outcome come from task_runs.
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,                    -- e.g. 'daily-summary'; passed as orchestrator.handle(trigger=...)
  skill_id INTEGER NOT NULL REFERENCES skills(id),  -- specific version; its skill_tools limit what a run may do
  params TEXT CHECK (params IS NULL OR json_valid(params)),  -- JSON values for the skill's template variables
  cron_schedule TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'UTC',  -- IANA name for CronTrigger(timezone=...); a bare cron string is ambiguous across DST
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
  next_run_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, name)
);
CREATE INDEX tasks_due ON tasks(status, next_run_at);
CREATE INDEX tasks_by_skill ON tasks(skill_id);

-- One row per run. Outcomes are stored here rather than in tasks.status, so a failed run
-- doesn't overwrite the task's active/paused state and past runs are kept.
CREATE TABLE task_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,  -- channel='task' conversation with the run's messages
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
  error TEXT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  CHECK ((status = 'running') = (finished_at IS NULL))
);
CREATE INDEX task_runs_by_task ON task_runs(task_id, started_at);
CREATE INDEX task_runs_by_conversation ON task_runs(conversation_id);
-- At most one run in progress per task. On startup the scheduler should mark leftover
-- 'running' rows as 'failed' (they belong to a process that crashed).
CREATE UNIQUE INDEX task_runs_one_running ON task_runs(task_id) WHERE status = 'running';

-- Seed data: the tools that exist in app/tools/ -----------------------------------------

INSERT INTO tools (name) VALUES
  ('calendar'),                          -- app/tools/calendar_tool.py
  ('notes');                             -- app/tools/notes_tool.py

INSERT INTO tool_scopes (tool_id, scope, description) VALUES
  ((SELECT id FROM tools WHERE name = 'calendar'), 'read',  'list_events'),
  ((SELECT id FROM tools WHERE name = 'calendar'), 'write', 'create_event'),
  ((SELECT id FROM tools WHERE name = 'notes'),    'read',  'read_notes'),
  ((SELECT id FROM tools WHERE name = 'notes'),    'write', 'create_note');

PRAGMA user_version = 2;                 -- keep in sync with SCHEMA_VERSION in app/db.py

COMMIT;
