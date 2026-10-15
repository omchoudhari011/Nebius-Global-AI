# Custodian

Custodian is a private, single-user AI assistant that you host yourself. It has a lifetime
memory: every chat, including a brand-new one, gets everything it has saved about you and
whatever you said in earlier conversations that bears on your message, found by keyword and,
with an embedding model, by meaning ("brolly" finds "umbrella"). It uses your calendar
and notes only after you switch each kind of access on, and runs scheduled jobs such as a
9:00 daily summary. The model is NVIDIA Nemotron on
[Nebius Token Factory](https://tokenfactory.nebius.com), or an offline stub for development.

Every reply is checked against what actually happened before you see it, so the assistant
can't claim it added an event that was blocked or remember something you never said
([Accuracy checks](#accuracy-checks)). You can see where your data goes, export or erase it,
keep chats for a limited time, chat off the record, and keep secrets away from a cloud model
([Privacy](#privacy)). It's sold open core: Free to self-host, Pro and Cloud through license
keys that are checked offline ([Plans and licensing](#plans-and-licensing)).

Day to day it sets reminders that notify you, answers questions about files you give it
(PDFs, photos), takes voice messages, works with your Google Calendar, Gmail and Drive when
you allow it, can hand hard questions to a bigger model, and learns from the replies you rate
([Everyday features](#everyday-features)).

- **Backend** (`backend/`): FastAPI, SQLite with FTS5, NumPy for vector search, APScheduler.
  Message text, tool arguments and results, memories and their embeddings are encrypted with
  Fernet before they are saved.
- **App** (`mobile/`): Expo SDK 57 (React Native) for iOS, Android and the web, with light
  and dark mode.
- **One Docker image** serves the API and the web app from the same origin.

```
Nebius-Global-AI-main/
├── backend/            API, agent loop, tool sandbox, scheduler, tests and evals
│   ├── app/            Python package (see "How it works")
│   ├── skills/         versioned prompt templates, synced into the database on start
│   ├── eval/           memory, lifetime-chat and paraphrase recall, latency, permission-block,
│   │                   hallucination-guard and security evals -> results.md
│   ├── tests/          pytest suite
│   ├── finetune/       dataset builder and LoRA training for a Custodian-tuned model
│   └── schema.sql      21 tables, triggers, FTS5 index (design notes: SCHEMA.md)
├── mobile/             Expo app: Chat, Scheduled, Tools & Data, Account, sign-in
├── store/              sells Pro and Cloud through Stripe; issues keys, hosts Cloud tenants
├── deploy/Caddyfile    HTTPS reverse proxy for the `tls` compose profile
├── run-local.ps1       Windows: start the API and web app together on one port
├── Dockerfile          builds the web app, then the Python runtime
└── docker-compose.yml
```

## Run it locally

You need Python 3.11+ and Node 20+.

**Quickest on Windows**, once the backend's `.venv` exists (step 1): `.\run-local.ps1` builds
the web app if needed and serves it with the API at http://localhost:8000 (`-Port 8001` if
8000 is taken, `-Rebuild` after changing `mobile/`).

**1. Backend** (from `backend/`):

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m app.cli seed-demo          # optional: demo memories, events, notes and a daily task
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

With no `.env`, development mode generates its secrets into `data/.dev-secrets.json` and
logs the sign-in passcode at startup (`Development passcode: …`). To choose your own, run
`python -m app.cli init-env` and edit `backend/.env`.

**2. Web app** (from `mobile/`, in a second terminal):

```bash
npm install
npm run web                          # opens http://localhost:8081
```

**3. Phone** (optional): install Expo Go (it must support SDK 57), run `npx expo start` in
`mobile/`, and scan the QR code. On the sign-in screen set the server to your computer's LAN
address, e.g. `http://192.168.1.20:8000` (a phone can't reach your computer's `localhost`).

The default model is the offline stub: it answers from simple rules but drives the real
loop (skills, permission gate, sandbox, memory). For real answers set `MODEL_BACKEND=nebius`
and `NEBIUS_API_KEY` in `backend/.env`.

**Search by meaning** (optional): with [Ollama](https://ollama.com) running, run
`ollama pull nomic-embed-text` and set `EMBEDDING_MODEL=nomic-embed-text` and
`EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1` in `backend/.env` (use `127.0.0.1`, not `localhost`:
on Windows each new connection to `localhost` waits ~2 s for IPv6 first). On the next start the
server embeds your existing memories and messages in the background; without it, recall uses
keywords only.

Try: “What's on my calendar today?” (blocked: every switch starts off), then switch on
**Calendar · Read** in Tools & Data and ask again. Then “Remember that I prefer meetings
before noon”, and run the daily summary from the Scheduled tab. To see the lifetime memory,
mention something in passing (“I lent my umbrella to Kavya”), press **New chat**, and ask
“Who has my umbrella?”: Active Context shows the earlier message it used. The eye button next
to **New chat** starts an off-the-record chat, and the **Account** tab shows your plan, usage,
where your data goes, and the export and erase buttons.

## Deploy

```bash
cd backend && python -m app.cli init-env --production --timezone Asia/Kolkata && cd ..
# edit backend/.env: MODEL_BACKEND=nebius and NEBIUS_API_KEY=...
docker compose up -d --build                    # http://<host>:8000
```

`init-env` prints the sign-in passcode once; it's also in `backend/.env`.

**HTTPS** (recommended for anything beyond your own machine): point a DNS name at the
server, open ports 80 and 443, and run

```bash
DOMAIN=custodian.example.com CUSTODIAN_BIND=127.0.0.1 docker compose --profile tls up -d --build
```

`CUSTODIAN_BIND=127.0.0.1` keeps port 8000 off the network so nobody can skip HTTPS and
send the passcode in plain text; leave it out only if you also need direct LAN access.

Caddy obtains and renews a Let's Encrypt certificate. The API trusts `X-Forwarded-For` only
from Caddy's fixed address on the compose network (`FORWARDED_ALLOW_IPS`), so clients that
reach port 8000 directly can't fake their IP to dodge the sign-in rate limit; if you put a
different proxy in front, set `FORWARDED_ALLOW_IPS` to its address. Production cookies are `Secure`, so
over plain HTTP on a LAN (not `localhost`) also set `COOKIE_SECURE=false` in `backend/.env`.

The container runs as an unprivileged user with a read-only root filesystem and no Linux
capabilities; everything it writes lives in the `custodian-data` volume (`/data`). Run a
single worker (the default): the scheduler lives inside the API process.

**Operations** (inside the container: `docker compose exec custodian python -m app.cli …`):

| Command | What it does |
|---|---|
| `check` | Validates settings and the database; prints a summary without secrets |
| `backup /data/backups` | Consistent copy of every database, safe while running |
| `rotate-keys` | Re-encrypts all private columns with the first key in `APP_SECRET_KEY` |
| `run-task daily-summary` | Runs a scheduled task now and prints its output |
| `sandbox-selftest` | Proves tools can't reach the network, other files, processes or secrets |
| `seed-demo` | Adds demo data |
| `export FILE` | Writes everything you've stored, decrypted, to a JSON file |
| `license-keygen` · `license-issue` | For the vendor: create the license signing key, sign a customer's key (see below) |

**Back up `APP_SECRET_KEY` with your data.** Without it, stored messages and memories can't
be decrypted. To rotate it: set `APP_SECRET_KEY=new_key,old_key`, restart, run
`rotate-keys`, then remove `old_key`.

**Native apps**: `cd mobile && npx eas build --profile production` (needs an Expo account;
the app id is `io.github.omchoudhari011.custodian` in `app.json`; a fork should use its own). Release
builds should talk to an HTTPS server; enter its address on the sign-in screen.

## Everyday features

Each one has its own switch in **Tools & Data** (all off until you turn them on), runs through
the same permission gate, sandbox and accuracy checks as everything else, and is listed under
**Account → Where your data goes** once it's set up.

**Reminders** ("remind me to call mum at 6pm", "every weekday at 10am…"). The reminders tool
stores them; every minute the server turns the ones that are due into a notification in the
app's inbox (the bell in the chat header). The web app also shows a browser notification
while it's open, and phones schedule each upcoming reminder as a local notification, so it
goes off on time even offline. For a push to your phone from the server, install the
[ntfy](https://ntfy.sh) app, subscribe to a long random topic, and set
`NTFY_URL=https://ntfy.sh/<that-topic>` (or your own ntfy server). Pushes say only "Reminder
from Custodian" unless you set `NTFY_SHOW_TEXT=true`. Finished scheduled tasks land in the
inbox too. The accuracy check now flags "I'll remind you" only when no reminder was set,
and still flags promises to text, email or call you, which Custodian can't do.

**Files**: attach a PDF, text file, web page or photo with the paperclip and it is read with
that message (and follow-ups in the same chat) - attaching it is the permission, so no switch
is needed. Files you add in Tools & Data stay available: ask about them later in everyday words ("what does my lease say about pets?"). The server
extracts the text, judging the file by its content rather than its name: a PDF's text layer,
or, for photos, a description and transcription by a vision model (`VISION_MODEL`, for
example `llava` in Ollama on the same machine). The model reaches it only through **Files ·
read**. Small vision models can misread numbers in a photo; use a stronger one for receipts.

**Voice**: the microphone records a message, the server transcribes it with a
speech-to-text server, and the text lands in the message box for you to check before
sending. Replies have a speaker button that reads them aloud with the device's own voice.
For a Whisper server on this machine (nothing leaves it):

```bash
docker run -d --name whisper -p 9000:9000 hwdsl2/whisper-server   # prints an API key on first start
# backend/.env:
WHISPER_URL=http://127.0.0.1:9000/v1
WHISPER_API_KEY=<the key it printed>
```

**Google Calendar, Gmail and Drive**: create an OAuth client at
[console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
(type "Web application"; enable the Calendar, Gmail and Drive APIs; add your server's
`…/integrations/google/callback` as an authorised redirect URI), then set
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI` and press **Connect
Google** in Tools & Data. Gmail and Drive are read only. The refresh token stays encrypted
on the server; a Google tool gets a short-lived access token, and its sandbox may reach only
Google's API hosts (every other address stays blocked). Email and file contents are treated
as information, never as instructions.

**Think harder**: with `LARGE_MODEL_ID` set (for example
`nvidia/nemotron-3-super-120b-a12b` on Nebius Token Factory, with `LARGE_MODEL_API_KEY`), the
chat shows a **Think harder** switch that sends the next message to that model, and a reply
that fails the accuracy check twice is handed to it automatically. When it runs on the
internet, secrets are withheld from it as from any online model.

**Rate replies, and teach the model**: every reply has thumbs up and down. A thumbs down asks
what it should have said. Tick **Use this to improve the model** and the reply - with the
exact prompt behind it, kept encrypted with the chat - becomes training data the next time
you run `backend\finetune\run_finetune.ps1` (`finetune/from_feedback.py` collects it; each
counts three times). Off-the-record chats and anything that looks like a secret are never
used, and nothing is sent anywhere.

## Plans and licensing

Custodian is open core. Everything about privacy and safety is in every plan; what you pay
for is capability and convenience (`app/plans.py`):

| | Free | Pro | Cloud |
|---|---|---|---|
| Price (change it in `app/plans.py`) | $0 | $5 / month, billed yearly | $12 / month |
| Who runs it | You, with your own model | You, with your own model | We do, model included |
| Lifetime memory, calendar, notes | Keyword search | Keyword **and meaning** search | Keyword and meaning search |
| Memories | Up to 500 | No limit | No limit |
| Scheduled tasks | 1 | Up to 25 | Up to 25 |
| Model usage | Your own, unmetered | Your own, unmetered | 5M tokens a month included |
| Export, erase, off-the-record chats, retention, secret withholding, accuracy checks | ✓ | ✓ | ✓ |

**Which plan applies**: a valid license key (`CUSTODIAN_LICENSE_KEY`, or pasted into
Account), otherwise Free. In development every feature works without a key (a Pro preview);
set `CUSTODIAN_PLAN=free` to see the app as a Free user would. Limits are enforced on the
server: past one, the API answers `402` with what to do. A downgrade never deletes anything:
memories stay (new ones wait for room), and only the oldest active tasks keep running.

**License keys are checked offline.** A key is a small signed document (plan, licensee,
expiry) verified with an Ed25519 public key built into the app, so a self-hosted server never
calls home, not even about its license. An expired key keeps working for a 7-day grace period.

**Selling licenses** (once, as the vendor):

```bash
python -m app.cli license-keygen     # writes ~/.custodian/license-signing-key.pem and prints a public key
# paste that public key into VENDOR_PUBLIC_KEYS in backend/app/licensing.py and release that build
python -m app.cli license-issue --plan pro --to "Customer Name" --days 365   # prints the key to send them
```

Keep the signing key off customer servers and out of the repository. This build already
trusts one public key (October 2026), whose private half is in
`~/.custodian/license-signing-key.pem` on the machine that created it: **back that file up**,
since without it no new license can be issued.

**Selling automatically**: [store/](store/README.md) takes payment through Stripe, issues
the key on the buyer's order page, and for Cloud also starts the buyer's own Custodian
container on its own subdomain (one isolated instance per customer, since Custodian is
single-user). Set `CUSTODIAN_PURCHASE_URL` to the store's address to show a **Get a license**
button in the Account tab.

**Usage metering** (`app/usage.py`): every model call is counted per day, with the token counts
the model service reports (estimated from text length when it reports none, and labelled as an
estimate). The Account tab shows this month and today; with `MODEL_PRICE_INPUT_PER_M` and
`MODEL_PRICE_OUTPUT_PER_M` set (USD per million tokens) it also estimates the cost. Without
them no cost is shown rather than a guessed one. On Cloud, chats and task runs stop with a
clear message when the monthly allowance is used up.

## Fine-tuning the model

Small local models skip saving what you tell them and describe things they never checked.
`backend/finetune/` trains a LoRA adapter for the local Nemotron model on synthetic
conversations built from Custodian's own prompts and tools, kept only when the app's accuracy
checks pass them, then installs the result in Ollama as `custodian-nemotron`:

```powershell
cd backend; .\finetune\run_finetune.ps1    # needs an NVIDIA GPU (8 GB+) and Docker Desktop
```

`finetune/compare.py` measures the base and tuned models on held-out conversations and the
app's evals. Details: [backend/finetune/README.md](backend/finetune/README.md).

## Configuration

Set in `backend/.env` (see `backend/.env.example` for every option).

| Variable | Default | Purpose |
|---|---|---|
| `CUSTODIAN_ENV` | `development` | `production` requires every secret and disables `/docs` |
| `CUSTODIAN_PASSCODE` | generated in dev | Sign-in passcode (10+ characters in production) |
| `APP_SECRET_KEY` | generated in dev | Fernet key(s), comma-separated for rotation |
| `SESSION_SECRET` | generated in dev | Signs session tokens (32+ characters in production) |
| `CUSTODIAN_TIMEZONE` | `UTC` | Calendar times and default for scheduled tasks |
| `MODEL_BACKEND` | `stub` | `stub` (offline) or `nebius` |
| `NEBIUS_API_KEY` | | Required for `nebius` |
| `NEBIUS_MODEL_ID` | `nvidia/nemotron-3-super-120b-a12b` | Any tool-calling model on Token Factory |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1` | OpenAI-compatible endpoint |
| `MODEL_EXTRA_BODY` | | JSON merged into each model request |
| `EMBEDDING_MODEL` | (off) | Embedding model for search by meaning, e.g. `nomic-embed-text` |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | `NEBIUS_BASE_URL` / `NEBIUS_API_KEY` | Any OpenAI-compatible `/embeddings` endpoint (Ollama: `http://127.0.0.1:11434/v1`). `NEBIUS_API_KEY` is only used for the Nebius endpoint; another host gets `EMBEDDING_API_KEY` or no key |
| `EMBEDDING_MIN_SIMILARITY` | `0.5` | Least cosine similarity for a meaning match |
| `DATA_DIR` | `backend/data` (`/data` in Docker) | Database, tool data, dev secrets |
| `CORS_ORIGINS` | `http://localhost:8081` in dev | Web origins allowed to call the API |
| `COOKIE_SECURE` / `COOKIE_SAMESITE` | `true` in production / `lax` | Session cookie flags |
| `TOOL_TIMEOUT_SECONDS` | `5` | Wall-clock limit per tool call |
| `SCHEDULER_ENABLED` | `true` | Run scheduled tasks in this process |
| `SESSION_MAX_AGE_DAYS` | `30` | How long a sign-in lasts |
| `CUSTODIAN_LICENSE_KEY` | | A Pro or Cloud license key (wins over one pasted into Account) |
| `CUSTODIAN_PLAN` | `pro` in development | Development only: preview `free`, `pro` or `cloud` without a license |
| `CUSTODIAN_PURCHASE_URL` | (unset) | Where **Get a license** goes (your store); unset hides it |
| `NTFY_URL` / `NTFY_SHOW_TEXT` | (unset) / `false` | Push reminders to your phone through ntfy; include their text or not |
| `WHISPER_URL` / `WHISPER_API_KEY` | (unset) | OpenAI-compatible speech-to-text server for voice messages |
| `VISION_MODEL` / `VISION_BASE_URL` / `VISION_API_KEY` | (unset) / `NEBIUS_BASE_URL` | Vision model that reads uploaded photos, e.g. `llava` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | (unset) | Your Google OAuth client, for Calendar, Gmail and Drive |
| `LARGE_MODEL_ID` / `LARGE_MODEL_BASE_URL` / `LARGE_MODEL_API_KEY` | (unset) / Token Factory / `NEBIUS_API_KEY` | The larger model for **Think harder** and escalation |
| `MODEL_PRICE_INPUT_PER_M` / `MODEL_PRICE_OUTPUT_PER_M` | (unset) | USD per million prompt / completion tokens, for cost estimates |

## How it works

Every chat message and every scheduled run goes through one agent loop,
`orchestrator.handle()`:

1. Save the incoming message (encrypted).
2. Pick a skill by keyword (`app/skills.py`); scheduled tasks use their pinned version.
3. Gather the lifetime memory, in every chat including a new one. This is retrieval-augmented
   generation with **hybrid search**: keyword search (SQLite FTS5, BM25 ranking) and, when
   `EMBEDDING_MODEL` is set, search by meaning (embeddings, cosine similarity,
   `app/vectors.py`), the two rankings merged by reciprocal rank fusion. A meaning match must
   also stand out from the query's other scores, so "hello" doesn't drag in random messages.
   - **Every saved memory** that fits the prompt budget (about 1,500 tokens), with the best
     matches and standing preferences first, so the relevant ones always get in
     (`app/memory.py`).
   - **What you said in earlier conversations**: up to 6 of your past messages, from any chat
     and any date, that best match this one (`app/history_search.py`). Only your own statements
     are searched; Custodian's past replies, your earlier questions, small talk and anything that
     looks like a secret aren't, so an old mistake or a password can't come back. This finds
     things you mentioned in passing that were never saved as memories, even in other words.
4. Call the model with the skill's prompt (with your memories), the recent history of this
   chat, your message (with any earlier messages quoted before it, never in the system prompt)
   and the tools that skill allows. If the model runs on the internet, secrets in all of it are
   replaced with placeholders first ([Privacy](#privacy)); every call is metered.
5. Send each proposed tool call through the gate (`app/tools/sandbox.py`): log it, validate
   the arguments, check the tool is enabled, **your switch is on** and **the skill allows the
   scope**, then run it in a separate locked-down Python process.
6. Feed results back to the model (up to 4 rounds). Check the final answer against what the
   turn really did, and give the model one chance to fix a false claim
   ([Accuracy checks](#accuracy-checks)).
7. Save the reply, link the memories and earlier messages it was given to it, and return
   `{reply, used}`. `used` lists the memories (`matched` marks the ones picked for this
   message), the earlier messages, tool calls, skill version, anything saved to memory and the
   checks the reply went through; the app shows it under each reply and in Active Context.

### Accuracy checks

Models, small local ones especially, say "I've added it to your calendar" when the call was
blocked, describe a calendar they never read, or save a "memory" you never said, which then
comes back as fact in every later chat. Custodian checks the work instead of trusting the
prompt (`app/grounding.py`):

- **Memories must be your own words.** Every number and name in a memory the model saves has to
  appear in what you said (this message, this chat, or your earlier messages it was shown), and
  most of its other words too. "Prefers meetings before noon" from "I prefer meetings before
  noon" is kept; "Flying to Goa on 22 October" from "I'm flying to Goa next week" isn't. The
  model is told why and can save it again in your words. Scheduled runs save no memories.
- **Replies must match what happened.** Each final answer is compared with the turn's record:
  which tool calls succeeded, which were blocked or failed, what was saved, which memories
  existed. Claiming an event or note that wasn't created, promising a reminder (Custodian can't
  send any), answering as if a blocked tool had worked, describing the calendar without reading
  it, "remembering" a secret or an off-the-record chat, or citing a memory number that doesn't
  exist all fail the check.
- **One repair round, then a visible note.** A failing draft is never shown or kept in the
  chat history. The model gets one more turn, with its tools, so it can do a step it skipped
  or say plainly what didn't happen. If the rewrite still fails, the reply ends with a
  `Custodian check:` note saying what really happened. The app marks such replies
  ("Corrected before sending" / "Unverified claim flagged") and Active Context lists the details.
- **Saying "I don't know".** When you ask about yourself and nothing saved or said matches,
  the model is told so and asked not to guess.

The checks are deterministic patterns rather than a second model judging the first: cheap,
predictable, and biased towards not flagging true statements. `eval/grounding_eval.py` measures
them, and with `--model nebius` measures how often the real model's first draft needed them.

### Privacy

Everything here is in every plan, on the **Account** tab (`app/privacy.py`):

- **Where your data goes**: the model and the embedding service, each shown as offline, this
  device, your local network, or the internet.
- **Secrets stay on your server.** When the model is on the internet (or always, if you
  choose), passwords, PINs, one-time codes, card, account and ID numbers and API keys are
  replaced with placeholders such as `[secret withheld]` in everything sent to it, including
  tool results. Your own stored copy keeps the original, encrypted. A model on your own machine
  gets the message as typed.
- **Off-the-record chats** are never saved to memory, never recalled in other chats, never
  embedded, and are deleted 24 hours after the last message.
- **Retention**: delete chats older than 7, 30, 90 or 365 days, checked hourly. Memories stay
  until you delete them.
- **Export** downloads everything Custodian holds about you, decrypted, as one JSON file (also
  `python -m app.cli export`). **Erase** deletes every chat, memory, task, tool call, note and
  calendar event, then rebuilds the database files so the deleted text doesn't linger in them.

### Security model

- **Default deny.** A tool call needs the tool enabled, your switch on and the skill's
  allowlist. The check reads the database immediately before the call; the model's output is
  never trusted. Every attempt, blocked or not, is logged in `tool_calls`; every switch
  change is logged by a database trigger.
- **Encryption at rest** for message text, tool arguments and results, and memories (Fernet,
  key rotation supported). **Known tradeoff:** the keyword index `memories_fts` keeps a
  plaintext copy of memory text in the same database file so memories can be searched. The
  search over past chats has no such copy: its index is built in the server's memory from the
  decrypted messages and is never written to disk (the first chat after a restart pays a
  one-off cost to build it). Embeddings are stored Fernet-encrypted too, since a vector can be
  partly turned back into its text, and anything that looks like a secret is never embedded.
  With a cloud `EMBEDDING_BASE_URL`, each memory and message is sent there once to be embedded;
  the default for a local setup is Ollama on your own machine.
- **Memory guardrails.** Passwords, PINs, one-time codes, card and bank account numbers, ID
  numbers (Aadhaar, PAN, passport, SSN) and API keys are never saved as memories, whether the
  model, the server or you ask (`app/sensitive.py`), and a message containing one is never
  recalled into a later chat, so a secret typed once doesn't keep reaching the model, or a
  cloud provider. Old messages are quoted in your turn as records, never put in the system
  prompt, and every system prompt says quoted text and tool results are information, not
  requests, so an instruction hidden in something pasted weeks ago doesn't come back as a
  rule. One reply can save at most 3 memories, and every save is shown under the reply.
- **Cost guardrails.** Each scheduled run calls the model, so schedules must leave at least 15
  minutes between runs, and at most 48 task runs a day (scheduled and "Run now" together)
  reach the model; later ones are recorded as skipped, with the reason, in the Scheduled tab.
- **Tool sandbox.** Tools run in a separate `python -I` process with a scrubbed environment
  (no API or encryption keys) and a wall-clock timeout. An audit hook blocks sockets/DNS,
  starting processes, native libraries via ctypes, and file access outside the tool's own
  folder; on Linux it also caps CPU, memory and file size. Tools keep their data in
  `DATA_DIR/tools/<tool>/` and never open the main database. This hardens our own tool code
  against hostile arguments; it is not an OS-level sandbox, which is why the container also
  runs unprivileged and read-only.
- **Sign-in**: one passcode, constant-time comparison, 5 attempts per 15 minutes per client
  and 50 from all clients together (counted before the passcode is checked, so parallel
  requests can't slip past). Every sign-in is a server-side session (an httpOnly `SameSite`
  cookie on the web, the device keychain on phones): signing out ends it on the server, so a
  token copied off a lost phone stops working at once, and Account lists every signed-in
  device with a sign-out button for each. Changing `CUSTODIAN_PASSCODE` or `SESSION_SECRET`
  signs out every device.
- **HTTP hardening**: explicit CORS origins (never `*`); cookie-authenticated writes from
  another site's pages are refused (`Origin` / `Sec-Fetch-Site` check, on top of `SameSite`);
  request bodies over 256 KB are refused; API responses are `Cache-Control: no-store`; a
  strict Content-Security-Policy for the web app, `nosniff`, `X-Frame-Options: DENY`,
  no-referrer, request IDs.
- **Deleting really deletes**: SQLite runs with `secure_delete` and the memory search index
  is rebuilt after every delete or edit, so a deleted memory's plaintext search copy doesn't
  linger in the database file.

## API

All routes except `/health` and `/auth/login` need a session (cookie or `Authorization: Bearer`).

| Route | Purpose |
|---|---|
| `GET /health` | Liveness and database check |
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | Session |
| `GET /auth/sessions` · `DELETE /auth/sessions/{id}` · `DELETE /auth/sessions` | Signed-in devices; sign one out, or every other one |
| `POST /chat` | `{message, conversation_id?, private?, deep?, document_ids?}` → `{reply, used, conversation_id, message_id, private}` |
| `GET /conversations` · `GET/DELETE /conversations/{id}` | History, with `used` per reply |
| `GET /permissions` · `POST /permissions/{tool_id}` | Switches; body `{scope, granted}` |
| `GET /permissions/events` | Switch history |
| `GET/POST /memories` · `PATCH/DELETE /memories/{id}` | Memories |
| `GET /tool-calls` | Audit log of every proposed tool call |
| `GET /skills` | Active skill versions |
| `GET/POST /tasks` · `GET/PATCH/DELETE /tasks/{id}` | Scheduled tasks |
| `POST /tasks/{id}/run` · `GET /tasks/{id}/runs` | Run now; run history |
| `GET /features` | What this server has set up (voice, photos, larger model, Google, phone push), unread count |
| `GET /notifications` · `POST /notifications/{id}/read` · `POST /notifications/read-all` | The inbox |
| `GET /reminders` · `DELETE /reminders/{id}` | Upcoming reminders; cancel one |
| `GET/POST /documents` · `DELETE /documents/{id}` | Files: upload `{name, content_type, data}` (base64, up to 10 MB) |
| `POST /voice/transcribe` | Raw audio body → `{text}` |
| `POST/DELETE /messages/{id}/feedback` | Rate a reply: `{rating: 1 or -1, correction?, share}` |
| `GET /integrations` · `GET /integrations/google/start` · `DELETE /integrations/google` | Google connection |
| `GET /privacy` · `PATCH /privacy/settings` | Where data goes, what's stored; `{retention_days?, redact_secrets?}` |
| `GET /privacy/export` · `POST /privacy/erase` | Download everything; erase everything (body `{"confirm": "ERASE"}`) |
| `GET /billing` · `POST/DELETE /billing/license` | Plan, limits and usage; activate (`{key}`) or remove a license |

In development, interactive docs are at `/docs`.

## Tests and evals

```bash
cd backend
python -m pytest                     # unit, API and sandbox tests
python eval/run_all.py               # rewrites eval/results.md (memory, lifetime-chat and paraphrase recall, latency, blocking, hallucination guard, security)
python eval/run_all.py --model nebius  # the same against the real model
python scripts/demo_walkthrough.py   # the whole story in the terminal

cd ../mobile
npx tsc --noEmit && npx expo export --platform web
```

Current results are in [backend/eval/results.md](backend/eval/results.md). CI
(`.github/workflows/ci.yml`) runs all of the above and smoke-tests the Docker image.
