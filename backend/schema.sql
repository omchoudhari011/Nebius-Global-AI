-- Custodian schema — see docs/PLAN.md (Step 1: structure, Step 2: finalized alongside encryption).

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  type TEXT NOT NULL DEFAULT 'episodic',
  content BLOB NOT NULL,                 -- Fernet ciphertext, see app/crypto.py (Step 2)
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT
);

-- Keyword recall index. Holds a plaintext copy of memory text for FTS5 MATCH queries —
-- a disclosed tradeoff, see the Step 2 notes in docs/PLAN.md.
CREATE VIRTUAL TABLE memories_fts USING fts5(content);

CREATE TABLE skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  prompt_template TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  category TEXT,
  executor TEXT NOT NULL DEFAULT 'sandbox'
);

CREATE TABLE permissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  tool_id INTEGER NOT NULL REFERENCES tools(id),
  scope TEXT NOT NULL,
  granted INTEGER NOT NULL DEFAULT 0,    -- default-deny; see app/permissions.py (Step 3)
  granted_at TEXT,
  revoked_at TEXT,
  UNIQUE(user_id, tool_id, scope)
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  skill_id INTEGER REFERENCES skills(id),
  cron_schedule TEXT NOT NULL,
  next_run_at TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled'
);

CREATE TABLE conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  channel TEXT NOT NULL DEFAULT 'chat'
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  used TEXT,                             -- JSON: { "memories": [], "tools": [], "skill": null }
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
