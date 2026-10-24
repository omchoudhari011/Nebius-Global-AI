# Custodian schema (v8)

The DDL is in [schema.sql](schema.sql). This page has the diagram and the reasons behind the
design. The diagram is Mermaid: GitHub renders it, and you can paste it into
https://mermaid.live to export an image.

```mermaid
erDiagram
  users ||--o{ memories : owns
  users ||--o{ permissions : holds
  users ||--o{ conversations : has
  users ||--o{ tasks : schedules
  users ||--o| user_settings : chooses
  users ||--o{ sessions : "signs in as"
  users ||--o{ notifications : receives
  users ||--o{ integrations : connects
  messages ||--o| message_feedback : "rated by"
  users ||--o{ usage_daily : uses

  tools ||--|{ tool_scopes : defines
  tool_scopes ||--o{ permissions : "granted as"
  permissions ||--o{ permission_events : "history"
  tool_scopes ||--o{ skill_tools : "allowed in"
  tool_scopes ||--o{ tool_calls : "exercised by"

  skills ||--o{ skill_tools : allows
  skills ||--o{ tasks : runs
  skills |o--o{ messages : shapes

  tasks ||--o{ task_runs : fires
  conversations |o--o{ task_runs : records

  conversations ||--o{ messages : contains
  messages ||--o{ tool_calls : proposes
  tool_calls ||--o| messages : "answered by"
  messages ||--o{ message_memories : recalled
  memories ||--o{ message_memories : "recalled in"
  messages ||--o{ message_recalls : "given earlier messages"
  messages ||--o{ message_recalls : "recalled as earlier"
  memories ||--o{ memory_vectors : "embedded as"
  messages ||--o{ message_vectors : "embedded as"
  messages |o--o{ memories : "source of"
  memories ||--o| memories_fts : "indexed by (rowid)"

  users {
    int id PK
    text email UK
    text created_at
  }
  notifications {
    int id PK
    int user_id FK
    text kind "reminder | task | system"
    blob title "Fernet"
    blob body "Fernet"
    text ref
    text created_at
    text read_at
  }
  message_feedback {
    int message_id PK, FK
    int rating "1 | -1"
    blob correction "Fernet"
    int share "may train the model"
    text created_at
    text updated_at
  }
  integrations {
    int user_id PK, FK
    text provider PK "google"
    text account
    text scopes
    blob refresh_token "Fernet"
    blob access_token "Fernet"
    text expires_at
  }
  sessions {
    int id PK
    int user_id FK
    text token_hash UK "SHA-256 of the id in the token"
    text client "web | native"
    text device "Chrome on Windows"
    text created_at
    text last_seen_at
  }
  user_settings {
    int user_id PK, FK
    int retention_days "NULL = keep chats"
    text redact_secrets "auto | on | off"
    text license_key "signed; checked offline"
    text updated_at
  }
  usage_daily {
    int user_id PK, FK
    text day PK "UTC"
    int chat_turns
    int task_runs
    int model_calls
    int estimated_calls
    int prompt_tokens
    int completion_tokens
  }
  tools {
    int id PK
    text name UK
    text category
    text executor "sandbox | openshell | nemoclaw"
    int enabled
  }
  tool_scopes {
    int tool_id PK, FK
    text scope PK "read | write"
    text description
  }
  permissions {
    int id PK
    int user_id FK
    int tool_id FK "with scope -> tool_scopes"
    text scope FK
    int granted "0 = deny (default)"
  }
  permission_events {
    int id PK
    int permission_id FK
    int granted
    text changed_at "written by trigger"
  }
  skills {
    int id PK
    text name UK "UNIQUE(name, version)"
    int version UK
    text prompt_template "immutable"
    int active
    text created_at
  }
  skill_tools {
    int skill_id PK, FK
    int tool_id PK, FK
    text scope PK, FK
  }
  tasks {
    int id PK
    int user_id FK
    text name "UNIQUE(user_id, name)"
    int skill_id FK "specific version"
    text params "JSON"
    text cron_schedule
    text timezone "IANA"
    text status "active | paused"
    text next_run_at
    text created_at
  }
  task_runs {
    int id PK
    int task_id FK
    int conversation_id FK
    text status "running | succeeded | failed"
    text error
    text started_at
    text finished_at
  }
  conversations {
    int id PK
    int user_id FK
    text channel "chat | task"
    int private "off the record"
    text started_at
  }
  messages {
    int id PK
    int conversation_id FK
    text role "system | user | assistant | tool"
    blob content "Fernet"
    int skill_id FK
    int tool_call_id FK "role = tool only"
    text model
    int latency_ms
    blob checks "Fernet JSON: accuracy checks"
    blob trace "Fernet JSON: prompt and tools behind a final reply"
    text created_at
  }
  tool_calls {
    int id PK
    int message_id FK
    int tool_id FK
    text scope FK
    text function
    blob args "Fernet"
    text status "rejected | denied | running | succeeded | failed | timed_out"
    blob result "Fernet"
    text error
    int latency_ms
    text created_at
    text finished_at
  }
  memories {
    int id PK
    int user_id FK
    text type "episodic | semantic | preference"
    blob content "Fernet"
    int source_message_id FK
    text created_at
    text updated_at
    text expires_at
  }
  memories_fts {
    int rowid PK "= memories.id"
    text content "plaintext copy (FTS5, porter)"
  }
  message_memories {
    int message_id PK, FK
    int memory_id PK, FK
    int matched "1 = picked for the message, 0 = rest of lifetime memory"
  }
  message_recalls {
    int message_id PK, FK "the assistant turn"
    int recalled_id PK, FK "an earlier user message"
  }
  memory_vectors {
    int id PK "only grows"
    int memory_id FK "UNIQUE(memory_id, model)"
    text model
    blob vector "Fernet; NULL = never embedded"
  }
  message_vectors {
    int id PK "only grows"
    int message_id FK "UNIQUE(message_id, model)"
    text model
    blob vector "Fernet; NULL = never embedded"
  }
```

## What v8 adds: the web and your PC

| Need | v8 |
|---|---|
| Answer questions about the world | A `tools` row for `web` with one scope, `read` (search, and reading a public web page). Nothing is stored: results go to the model and the encrypted tool-call log. |
| Control the user's PC | A `tools` row for `pc` with `read` (system info, files, windows, clipboard, screenshots) and `write` (open apps, media, volume, lock, typing, clicks, file changes). Pending actions wait in memory for the desktop app (`app/desktop.py`), not in the database; every call is in `tool_calls` like any other tool. |
| Existing v7 databases | `db.MIGRATIONS[7]` adds the two tools and their scopes; every switch starts off. |

## What v7 adds: reminders, files, Google, feedback

| Need | v7 |
|---|---|
| Tell you something without being asked (a reminder, a finished task) | `notifications`, Fernet-encrypted, shown in the app's inbox. Reminders themselves live in the reminders tool's own file (`DATA_DIR/tools/reminders/reminders.db`); the server delivers due ones every minute. |
| Files you upload | Stored as text in the documents tool's own file (`DATA_DIR/tools/documents/documents.db`, FTS5), like notes; nothing new in this database. |
| A connected Google account | `integrations`: the refresh and access tokens are Fernet-encrypted; a tool only ever gets a short-lived access token. |
| Learn from replies you rate | `message_feedback`, plus `messages.trace`: the exact prompt and tools behind each final reply (Fernet JSON, deleted with the chat, never kept for off-the-record chats), so a reply you share becomes an exact training example. |
| New tools and switches | `tools` rows for reminders, documents, gcal, gmail and gdrive with their `tool_scopes`; every switch starts off. |
| Existing v6 databases | `db.MIGRATIONS[6]` adds the column, the three tables and the tool rows in one transaction. |

## What v6 adds: signed-in devices

| Need | v6 |
|---|---|
| Signing out must stop a token copied off a lost phone, not just clear the cookie | `sessions`: one row per sign-in. The signed token carries a random id and only its SHA-256 is stored; `require_session()` needs the row to exist, so deleting it ends that token at once. |
| See and sign out devices from the app | The Account tab lists `sessions` (device described from the User-Agent, last used, refreshed at most every 5 minutes) and deletes one row or every row but the current one. |
| Expired sessions don't pile up | The hourly job deletes rows older than `SESSION_MAX_AGE_DAYS`. |
| Existing v5 databases | `db.MIGRATIONS[5]` creates the table. Tokens issued before it don't name a row, so every device signs in once more. |

## What v5 adds: accuracy checks, privacy controls and plans

| Need | v5 |
|---|---|
| Show whether a reply's claims were checked, corrected or flagged, also when the chat is reopened | `messages.checks` on the final assistant message: Fernet-encrypted JSON from `app/grounding.py` (status, what was found, memories refused, secrets withheld). `build_used` returns it as `used.checks`; older replies have NULL. |
| Chats that are never remembered or recalled | `conversations.private`. History search and embedding skip their messages, `remember` isn't offered, and the hourly job deletes them a day after the last message. |
| Retention and secret withholding are the user's choice | `user_settings.retention_days` and `redact_secrets`; a missing row means the defaults (keep chats, withhold secrets when the model is on the internet). |
| A license activated in the app | `user_settings.license_key`, verified offline on every plan check (`app/licensing.py`), so a tampered value is simply ignored. |
| Usage that survives deleting chats, without keeping content | `usage_daily`: counters per user and UTC day, upserted by `app/usage.py`. |
| Existing v4 databases | `db.MIGRATIONS[4]` adds the two columns and two tables in one transaction. |

## What v4 adds: search by meaning

| Need | v4 |
|---|---|
| Find "I lent my umbrella to Kavya" from "who has my brolly?" - no word in common | `memory_vectors` and `message_vectors` hold an embedding of every memory and user message. `app/vectors.py` searches them in RAM and its ranking is fused with the keyword ranking (reciprocal rank fusion). |
| A vector can be partly turned back into its text | `vector` is Fernet ciphertext, like the text it comes from; `rotate-keys` covers it. |
| Never send a secret to an embedding endpoint, and don't keep retrying it | A row with `vector IS NULL` marks an item deliberately not embedded. |
| Changing the embedding model | Rows are per `model`; the new model embeds everything again in the background and old rows are ignored. |
| Rows written by another process (a CLI run) | The `AUTOINCREMENT` id only grows, so each process loads the rows above the last one it saw. |
| Deleted or edited items | `ON DELETE CASCADE` removes a deleted item's vectors; editing a memory deletes its vector so it is embedded again. |
| Existing v3 databases | `db.MIGRATIONS[3]` creates both tables; the server embeds existing data on its next start. |

## What v3 adds: lifetime memory

Every chat, including a new one, now gets the user's whole lifetime memory that fits the
prompt, plus what they said in earlier conversations that matches the message.

| Need | v3 |
|---|---|
| Every memory reaches every chat, not just the top 5 keyword hits | `orchestrator` puts all memories that fit a budget in the prompt, keyword matches first. `message_memories.matched` keeps the difference visible: 1 for the ones picked for the message (what v2 linked), 0 for the rest. |
| Things said in passing in old chats, never saved as memories | `app/history_search.py` searches every user message by keyword. Its FTS5 index lives in the server's memory, built from the decrypted messages, so message text stays encrypted at rest (unlike `memories_fts`). |
| Show which earlier messages shaped a reply, and never point at deleted text | `message_recalls`, with `ON DELETE CASCADE` on both sides, so `used.earlier` is rebuilt from foreign keys like the rest of `used`. |
| Existing v2 databases | `db.MIGRATIONS[2]` adds the column and table in one transaction; old links get `matched = 1`. |

## What changed from the v1 diagram, and why

| v1 problem | Fix in v2 |
|---|---|
| `users` → `skills` line, but `skills` had no `user_id` | Skills are global; the line is gone. |
| `permissions` 1 — * `tools` (backwards) | `tools` 1 — * `tool_scopes` 1 — * `permissions`. |
| "runs" label sat inside `permissions` | It's on `skills` → `tasks`. |
| `granted` + `granted_at` + `revoked_at` could disagree, and an upsert lost earlier grants | `permissions.granted` is the only current state. Every change is appended to `permission_events` by trigger. |
| `scope` was free text; a typo silently never matched | `tool_scopes` lists the valid scopes, and `permissions`, `skill_tools` and `tool_calls` all have foreign keys to it. |
| No link between skills and tools | `skill_tools` is each skill's allowlist, checked in the sandbox together with the user's grants. |
| `skills.name` UNIQUE blocked multiple versions; editing in place changed live tasks | `UNIQUE(name, version)`, rows can't be changed (trigger), `tasks.skill_id` points at a specific version, and `active` allows rollback. |
| `tasks.status` mixed the task's state with run outcomes; no history | `tasks.status` is `active`/`paused` only; each run is a row in `task_runs`, with at most one in progress per task. |
| Cron without a timezone; no template inputs | `tasks.timezone` (IANA) and `tasks.params` (checked with `json_valid`). |
| Task output had nowhere to go | `task_runs.conversation_id` points to a `channel = 'task'` conversation. |
| No record of what tools actually did | `tool_calls` logs every proposed call, including rejected/denied ones. It feeds the permission-block rate and latency evals. |
| `messages.used` JSON could keep references to deleted memories | `messages.skill_id`, `message_memories` and `tool_calls`, all with foreign keys. |
| Chat text stored as plaintext next to encrypted memories | `messages.content`, `tool_calls.args` and `tool_calls.result` are Fernet BLOBs. |
| `memories_fts` had no link back to `memories`, so recall couldn't filter by user and deletes left plaintext behind | `rowid = memories.id`; recall joins on it; a trigger deletes the FTS row, including on cascades from a user delete. |
| No record of where a memory came from | `memories.source_message_id`. |
| `embedding_ref` in the diagram, but the code uses FTS5 | Removed. If vectors are added later, also store the embedding model so you know what to re-embed. |
| Foreign keys never enforced | `app/db.py` runs `PRAGMA foreign_keys = ON` for every connection (SQLite's default is off). |
| No enums, `ON DELETE` rules or indexes | `CHECK` constraints on every enum-like column; deleting a user cascades everywhere; indexes for the scheduler, recall and conversation queries. |
| No schema versioning | `PRAGMA user_version` (now 10); `init_db()` upgrades a v2 to v9 file in place with `db.MIGRATIONS` and refuses a v1 or unknown database instead of half-applying the schema. |

## Deliberately not added

- **A `user_preferences` table.** Fuzzy preferences ("prefers morning meetings") are recalled like
  any other memory, so they stay in `memories` with `type = 'preference'`. `user_settings` holds
  only switches the server itself acts on (retention, secret withholding, the license); the
  timezone lives on `tasks`.
- **`tasks.last_run_at`, `conversations.updated_at`.** These can be derived from `task_runs` and
  `messages`, so storing them would just create a second copy that can go out of date.
- **Tool input schemas in the database.** The pydantic models in `app/tools/` stay the single
  source of truth for arguments.

## Where tool data lives

Calendar events and notes are not in this schema. Each tool keeps its own SQLite file under
`DATA_DIR/tools/<tool>/` (`calendar/calendar.db`, `notes/notes.db`). Tools run in a separate
sandboxed process that may only open files in its own folder, so a tool never needs, or can
reach, `custodian.db` - including the `permissions` table that decides whether it may run.
`python -m app.cli backup` copies the main database and every tool database together, as one
encrypted file (`app/backup.py`; the scheduler makes one daily), and `restore` puts one back.
