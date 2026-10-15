# Custodian backend

FastAPI + SQLite. See the [root README](../README.md) for deployment, configuration and the
security model; this page is for working on the code.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

No `.env` is needed in development: missing secrets are generated into
`data/.dev-secrets.json` and the passcode is logged at startup. `python -m app.cli init-env`
writes a `.env` with fresh secrets if you'd rather set them yourself.

`--host 0.0.0.0` lets a phone on the same Wi-Fi reach the API; everything except `/health`
and `/auth/login` still requires the passcode.

## Layout

| Path | Role |
|---|---|
| `app/main.py` | App factory: startup, middleware, error mapping, serving the web build |
| `app/routers/` | HTTP routes: `auth`, `chat` (+ conversations), `data` (permissions, memories, audit log), `tasks` (+ skills), `privacy`, `billing` |
| `app/orchestrator.py` | The agent loop for chat and scheduled tasks |
| `app/grounding.py` | Accuracy checks: memories must be the user's words; replies are checked against what the turn did, with one repair round |
| `app/privacy.py` | Where data goes, secret withholding for an online model, retention, off-the-record chats, export and erase |
| `app/plans.py`, `app/licensing.py` | Plans and their limits; offline Ed25519 license keys |
| `app/usage.py` | Per-day counters of turns, model calls and tokens; cost estimates and the Cloud allowance |
| `app/tools/sandbox.py` | The tool gate and the subprocess executor |
| `app/tools/runner.py` | The locked-down process a tool runs in (stdlib only) |
| `app/tools/registry.py`, `schemas.py` | Tool functions the model can call and their argument models |
| `app/tools/calendar_tool.py`, `notes_tool.py` | The tools; data in `DATA_DIR/tools/<tool>/` |
| `app/memory.py` | Encrypted memories; every prompt gets all that fit, FTS5 keyword matches first |
| `app/history_search.py` | Hybrid search over everything you've said in every chat (in-memory FTS5 plus vectors, nothing in plaintext on disk) |
| `app/vectors.py` | Search by meaning: encrypted embeddings, an in-RAM matrix per kind, the "must stand out" rule, rank fusion |
| `app/embeddings.py` | Client for any OpenAI-compatible `/embeddings` endpoint (Ollama, Token Factory); off without `EMBEDDING_MODEL` |
| `app/sensitive.py` | What counts as a secret (credentials, card/account numbers, Aadhaar, PAN, passport, API keys): never saved or recalled |
| `app/permissions.py` | Default-deny switches |
| `app/skills.py` + `skills/*.md` | Versioned prompt templates |
| `app/tasks.py`, `app/scheduler.py` | Scheduled tasks and their runs |
| `app/conversations.py` | History and the `used` field |
| `app/model_backends/` | `stub` (offline) and `nebius` (Nemotron on Token Factory) |
| `app/auth.py`, `app/crypto.py`, `app/db.py`, `app/config.py` | Sessions, Fernet, SQLite (schema and in-place upgrades), settings |
| `app/cli.py` | Operator commands (`python -m app.cli --help`) |

## Adding a skill

Create `skills/<name>.md`:

```markdown
---
name: travel
description: Plans trips around the calendar.
keywords: trip, travel, flight, hotel
tools: calendar:read, notes:write
---
Prompt text. Placeholders: $now, $timezone, $memories, and any task params as $name.
```

On the next start it's saved as version 1. Editing the file later saves a new version; saved
versions are never changed (a trigger blocks it), and scheduled tasks keep the version they
were pinned to until you move them with `PATCH /tasks/{id}` `{"skill": "<name>"}`.

## Adding a tool

1. Write the functions in `app/tools/<tool>_tool.py`, each taking `ctx` (`user_id`,
   `data_dir`, `timezone`) plus its arguments, stdlib only, storing data under `ctx["data_dir"]`.
2. Add an argument model to `app/tools/schemas.py` and a `ToolFunction` to
   `app/tools/registry.py` with the scope it needs.
3. Add the `tools` and `tool_scopes` rows (the seed in `schema.sql` covers new databases;
   insert them by hand on existing ones).
4. List the scope in the skills that may use it.

## Changing the schema

Edit `schema.sql` (used for new databases), bump `SCHEMA_VERSION` in `app/db.py` and the
`PRAGMA user_version` at the end of `schema.sql`, and add a `MIGRATIONS` entry that upgrades
the previous version in one transaction. `init_db()` applies it on the next start; run
`python -m app.cli backup` first on a database you care about.

## Tests and evals

```bash
python -m pytest                     # tests/ and eval/test_*.py
python eval/run_all.py               # rewrites eval/results.md (offline stub model)
python eval/run_all.py --model nebius
python scripts/demo_walkthrough.py
```

Tests and evals always use a throwaway data directory and never read `backend/.env`
(except the `NEBIUS_*` settings for `--model nebius`).
