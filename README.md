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
([Everyday features](#everyday-features)). It looks things up on the web with sources, and,
with the desktop app, controls your Windows PC by voice - "Hey Jarvis, open Spotify and turn
the volume down" - and speaks its replies ([Jarvis: your PC and the web](#jarvis-your-pc-and-the-web)).

- **Backend** (`backend/`): FastAPI, SQLite with FTS5, NumPy for vector search, APScheduler.
  Message text, tool arguments and results, memories and their embeddings are encrypted with
  Fernet before they are saved.
- **App** (`mobile/`): Expo SDK 57 (React Native) for iOS, Android and the web, designed as
  a warm companion: it greets you by name for the time of day, shows a few things it
  remembers about you, and has a soft orb that breathes while it listens and thinks (still
  when the system asks for reduced motion). Light mode is cream and coral, dark mode warm
  charcoal and amber, set in Nunito; every text colour meets WCAG AA.
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
├── desktop/            Windows tray app: PC control, wake word, spoken replies
├── deploy/Caddyfile    HTTPS reverse proxy for the `tls` compose profile
├── deploy/searxng/     settings for the SearXNG web search (`web` compose profile)
├── run-local.ps1       Windows: start the API and web app together on one port
├── Dockerfile          builds the web app, then the Python runtime
└── docker-compose.yml
```

## Install on Windows

**Custodian Setup.exe** (build it with `.\installer\build.ps1`; 30 KB, made with the C# compiler
that comes with Windows) asks where to put Custodian, fetches it with git (installing git with
winget if needed) and runs `install.ps1`; inside a Custodian folder it just runs `install.ps1`.
`install.ps1` does everything, each step only when needed, and is safe to run again:

- Python 3.11+ and Node.js (offered through winget), the server's packages - with speech to text
  on the graphics card when there's an NVIDIA one (`WHISPER_URL=local`) - and `backend\.env` with
  fresh secrets and a sign-in passcode (shown once: write it down);
- the web app, the Jarvis desktop app and its voice, and with Ollama a local model (`-Model`,
  default `qwen3:8b`) and the search-by-meaning model on a new setup;
- shortcuts: Start menu > Custodian (**Jarvis**, **Stop Jarvis**, **Update Custodian**, the web
  app) and Jarvis on the desktop; `-Autostart` starts Jarvis when you sign in.

**Updates**: *Update Custodian* (or `.\update.ps1`, or `Custodian Setup.exe --update`) stops
Jarvis, pulls the newest code, installs what changed and starts it again; your data and
settings stay, and it refuses while you have local changes. The server checks GitHub twice a
day without ever asking for a sign-in (`GET /version`): Account shows "N updates waiting", and
the tray's *Check for updates* offers *Update now*.

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
| `backup [folder]` | One encrypted backup of every database now (the server also makes one daily), safe while running |
| `restore <file>` | With the server stopped: puts a backup in place; the data in use is moved aside, never deleted |
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
goes off on time even offline. For a push to your phone from the server, press **Set up** under
*Notifications on your phone* in the Jarvis tab: Custodian makes a topic of your own with a long
random name, and you subscribe to it in the free [ntfy](https://ntfy.sh) app (*Open in ntfy*, then
*Send a test*). Pushes say only "Reminder from Custodian" unless you switch on *Show the text*.
`NTFY_SERVER` points it at your own ntfy server; `NTFY_URL` (and `NTFY_SHOW_TEXT=true`) sets one
topic for the whole server instead. Finished scheduled tasks land in the inbox too. The accuracy
check flags "I'll remind you" only when no reminder was set, and promises to text or call you,
which Custodian can't do.

**Files**: attach a PDF, text file, web page or photo with the paperclip and it is read with
that message (and follow-ups in the same chat) - attaching it is the permission, so no switch
is needed. Files you add in Tools & Data stay available: ask about them later in everyday words ("what does my lease say about pets?"). The server
extracts the text, judging the file by its content rather than its name: a PDF's text layer,
or, for photos, a description and transcription by a vision model (`VISION_MODEL`, for
example `qwen3-vl:4b` in Ollama on the same machine). The model reaches it only through **Files ·
read**. Small vision models can misread numbers in a photo: on a test receipt `llava` made up the
café, the prices and the total, while `qwen3-vl:4b` read every figure right. A vision model on
this machine is let go two minutes after each use (`VISION_KEEP_ALIVE`), since it's big (4 GB
and more) and needed only now and then.

**Voice**: the microphone records a message, the server transcribes it with Whisper, and the
text lands in the message box for you to check before sending. Replies have a speaker button
that reads them aloud with the device's own voice. Whisper can run inside the server itself, on
the graphics card when there is one - no Docker, and nothing leaves the machine:

```bash
pip install -r backend/requirements-voice.txt   # faster-whisper, and NVIDIA's CUDA libraries
# backend/.env:
WHISPER_URL=local
```

With an NVIDIA card it uses `large-v3-turbo`, which hears Hindi, Marathi and Hinglish far better
than the small model and takes about 0.4 s a request on a laptop RTX 5050; without one, `small`
on the processor. The model loads when the desktop app hears "Hey Jarvis", so it loads while you
talk, and is let go after `WHISPER_IDLE_MINUTES` (default 10) without a recording. Pick another
with `WHISPER_LOCAL_MODEL`, or force the processor with `WHISPER_DEVICE=cpu`. Or use a Whisper
server instead:

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
Google** in Tools & Data. Drive is read only. Gmail is read (**Gmail · read**), and with **Gmail · write**
Custodian writes drafts and sends email: "reply to Priya that the numbers are final" shows you the
recipients and the exact text, in the app, on Telegram or on the PC (wherever you asked), and sends
only on **Allow once**; a scheduled task never sends, and addresses are only ones it has seen, never
guessed. (Connected before this? Connect Google again to grant drafts and sending.) The refresh token stays encrypted
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

## Jarvis: your PC and the web

**Web knowledge** (switch: **Web · read**). Questions about the world - news, weather, prices,
people, how-to - are answered from a web search, with the sources listed under the answer.
Search goes through [SearXNG](https://docs.searxng.org) on your own machine, which asks
public search engines without your identity:

```bash
cp deploy/searxng/settings.yml backend/data/searxng-settings.yml   # set secret_key to a long random string
docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8888:8080 \
  -v "$PWD/backend/data/searxng-settings.yml:/etc/searxng/settings.yml:ro" searxng/searxng:2026.10.16-ff8f5ddcc
# backend/.env (this is the default): SEARXNG_URL=http://127.0.0.1:8888
```

(With Docker Compose: `SEARXNG_SECRET=... docker compose --profile web up -d` and
`SEARXNG_URL=http://searxng:8080`.) `web_search` may reach only SearXNG; `web_read_page`, which
reads one result, may reach only public internet addresses - never this machine or your home
network - and page text is treated as information, never as instructions. With
`LARGE_MODEL_ID` set, questions about the world go to the larger model, which knows far more;
questions about you ("my", "me") and off-the-record chats stay on the local model.

**The desktop app** (`desktop/`, switches: **PC · read** and **PC · write**) is a tray app for
Windows that lets Custodian act on your PC, from plain requests:

| Say | It does |
|---|---|
| "open youtube", "open notepad", "open my downloads" | opens the site, app or folder |
| "take me to the Wikipedia page for Jägermeister" | opens the best page for it in your browser |
| "search Amazon for wireless earbuds" | opens that site's results (Amazon, Flipkart, YouTube, Google, Maps, ...) |
| "play Kalyani", "play Shape of You on Spotify" | finds it and starts playing; pause, skip and volume report what's playing |
| "dim the screen", "dark mode", "open Bluetooth settings", "10 minute timer" | brightness, theme, Settings pages, timers that ring and speak |
| "what apps are running?", "close Chrome" | lists apps and memory use; quits an app (it can still ask to save) |
| "organise my downloads", "make a folder called Trips" | sorts files into Images, Documents, ...; creates, copies, moves, deletes (to the Recycle Bin) |
| "write a leave letter in Notepad" | writes the whole text into a new Notepad or Word document |
| "research the best laptops under ₹80,000 and put it in a Word document" | searches several ways at once, reads the best pages (from different sites), writes a report citing them [1], [2], and saves it as a new Word file in Documents with the sources as links - only sources it really read |
| "send a WhatsApp to Tanishka saying I'll be late" | opens that chat, checks it really is Tanishka's, shows you the chat and text, and sends only on **Allow once**. A name heard or spelled differently still finds its chat ("Mronal" opens Mrunal, "Abhi Choudhury" Abhi Chaudhari) when one clearly fits; when two fit, or none, it names the closest and asks |
| "what did I miss?", "any new WhatsApp messages?" | reads your notifications (WhatsApp, Outlook, Teams, sites in Chrome, your phone's through Phone Link) and the unread counts, after **Allow once**, and sums them up; what's in them is never taken as instructions |
| "click Send in WhatsApp", "fill the search box with ..." | reads the window's buttons and fields by name, then clicks or fills them |
| "find my resume and open it", "close Chrome and lock the PC" | several steps in a row, each using the last one's result |

It also locks the screen, types, presses keys, uses the clipboard and takes screenshots it can
describe. The commands people say most - "next track", "pause", "volume 40", "put on Kalyani",
"dark mode", "10 minute timer", "lock my PC", "open Notepad", "battery?" - are recognised exactly
(`app/fastpath.py`) and run in about 0.2 s without the model, through the same permission gate,
audit log and accuracy checks; anything else, or an action that didn't succeed, goes to the
model. Arguments a small model reliably gets slightly wrong ("Perfect in spotify" as a YouTube
search) are mended before they're validated. A message is offered only the tools it's about (media, files, the screen, ...), so
the small model chooses well and answers fast. The app has no open port: it asks the server
for the next action (`/desktop/next`) and sends the result back. Anything risky - typing,
keys, clicks, closing a window or app, reading or changing files, the clipboard, screenshots -
shows a dialog on the PC naming exactly what will happen and is cancelled unless you click
**Allow once** within a minute. `C:\Windows`, `C:\Program Files` and `C:\ProgramData` are never
changed, Windows' own processes are never closed, and every allow or decline is logged in
`%APPDATA%\Custodian\desktop.log`. Chrome and Edge show web pages' buttons to Windows only when
started with an accessibility switch: run `desktop\run-desktop.ps1 -BrowserAccess` once (it adds
`--force-renderer-accessibility` to their shortcuts) to let Custodian click on web pages too.

It is also the voice assistant: say **"Hey Jarvis"** (or press **Ctrl+Alt+Space**), speak, and
the reply is spoken back. The wake word is detected on the PC by
[openWakeWord](https://github.com/dscripka/openWakeWord); nothing is recorded or sent before
it. Speech goes to your Whisper server (see Voice above), and replies are spoken by a
[Piper](https://github.com/rhasspy/piper) voice on the PC, or Windows' own voice.

```powershell
.\run-jarvis.ps1                    # everything at once: SearXNG (starting Docker Desktop), Ollama, the server, Jarvis
.\run-jarvis.ps1 -Stop              # stop the server and Jarvis
desktop\run-desktop.ps1             # just the desktop app (first run: makes desktop\.venv, downloads the voice, asks you to sign in)
desktop\run-desktop.ps1 -Install    # start it when you sign in to Windows (-Uninstall to undo)
```

Spoken to, it behaves like Jarvis: a J.A.R.V.I.S. HUD (an arc-reactor core that follows your
voice, live captions, the PC's vitals, your day and the weather; **Ctrl+Alt+J** for full
screen), a British voice that answers briefly and calls you "sir" (or what you set in the
Jarvis tab), follow-ups without the wake word until you say "that's all", instant "system
status" reports, and warnings on its own about a low battery, an overloaded PC or a full drive.
When memory runs out it names the apps you haven't used for ten minutes that hold the most
("Chrome and Steam are holding 2.2 GB...") and offers to close them: say "yes" or press the HUD's
button. Each is closed as if you clicked its X, so one with unsaved work still asks.
Typed chats keep Custodian's own voice.

Settings are in `%APPDATA%\Custodian\desktop.json` (server, wake word on or off and its
threshold, hotkey, speaking replies, voice, microphone, protected folders); the sign-in is kept
in Windows Credential Manager. See [desktop/README.md](desktop/README.md).

## More Jarvis

The **Jarvis** tab in the app holds most of these: the HUD (the PC's processor, memory, graphics
card, battery and drive, the weather and your day, with its arc reactor - the desktop app sends
its readings every 30 s, kept only in the server's memory), a remote for the PC, actions waiting
for your OK, routines, what Custodian did (with undo), the settings below, and the connected
accounts.

**Search inside your files by meaning** (Jarvis → Settings → *Search inside my files*). The
desktop app reads Documents, Downloads and Desktop (text, code, PDF, Word, PowerPoint, Excel,
OpenDocument), sends only new or changed files' passages, and the server embeds them with the
memory model: "find the PDF where I wrote about the Pune flat". Files and folders whose names say
they hold secrets are never read, passages that look like a password or card number are never
stored, and switching it off deletes the index.

**Learn from the big model** (distillation). `finetune/distill.py` has the larger model on Nebius
rewrite every training scenario's message the ways real people say it - Hinglish, typos,
voice-style, indirect, very short - keeps a rewrite only if every name, number, time and quoted
text survives, and then runs it through the real agent loop with the larger model answering.
A conversation is kept only if the accuracy checks pass it unchanged and it called the tools the
scenario expects. Only the synthetic world is sent; none of your conversations.

```powershell
cd backend; $env:NEBIUS_API_KEY = "..."
.\finetune\run_finetune.ps1 -Distill        # distil, then train, export, install and compare as usual
```

**Learn from your corrections, automatically** (Jarvis → Settings → *Learn from my corrections
overnight*). Each 👎 with "what it should have said" (shared for training) becomes a preference
pair: your correction preferred over the reply it replaced. Between 01:00 and 06:00, when the PC
has been idle for half an hour, is plugged in and there are at least three new ratings, the
desktop app runs `finetune/nightly.py`: it continues the adapter in use with DPO on the pairs
plus a sample of the usual conversations (so nothing else is forgotten), compares the result with
your model, and switches only if nothing got worse - no kind of request may fall by more than one
held-out conversation (v6 lost uploaded files, 4/6 to 1/6), recall may not fall, and no more first
drafts may make things up. Touching the keyboard or mouse stops it at once; you get a note in the
morning either way. Needs one full run of `run_finetune.ps1` first.

**See the screen** (`SCREEN_VISION_MODEL`, e.g. `ollama pull qwen2.5vl:7b`). "What's on my screen?",
"what does this error say?" (`pc_look`), and "click the blue Sign in button" (`pc_click_on`): one
**Allow once** covers looking, finding it, clicking, and looking again to check the screen
changed - the reply says so if nothing did. Screenshots are read and never kept.

**Real browser automation.** Custodian drives Chrome or Edge directly over the DevTools protocol,
in a browser profile of its own (sign in there to the sites you want it to use; your everyday
profile is never touched; the port listens on this PC only). Pages are read with their links,
buttons and fields numbered, then filled and clicked by number, several steps in a row, with
downloads into Downloads. Following a link or typing a search just happens; anything that submits,
buys, pays, orders, books, sends, posts or deletes asks **Allow once** on the PC naming the button
and the page, and a password field is never filled.

**A proactive Jarvis.** Reminders, meeting heads-ups ("Design review starts in 10 minutes - open
the link?", answered by button or by saying yes) and watcher alerts are spoken on the PC, never in
your quiet hours; the first time you use the PC in the morning it reads out your day, and at the
time you choose (Jarvis tab, default 21:00; tray: *Wrap up my day now*) it wraps the day up: what it
did for you, counted and never quoting a message, what's still to do, and tomorrow's first
appointments - in the inbox, on your phone, and said on the PC outside quiet hours. Watchers:
"tell me when the iPhone 16 on Amazon drops below 60000", a page changing, words appearing
("In stock"), an email from someone.

**Routines.** "Work mode" = open VS Code, volume 30, Do Not Disturb on; "Good night" = close
Chrome, alarm at 07:00, lock the PC. Make them by chat ("make a routine called ...") or in the
Jarvis tab; saying the name runs every step, with one approval on the PC for the whole list when a
step would ask. A routine can't delete or write files, type, click or send messages.

**How Jarvis hears** - tested like people use it ([voice-lab/](voice-lab/README.md)): 44 everyday
requests in 10 voices (British, American, Indian English, Hindi), said normally, in a hurry, with
someone talking and a fan going, and across a room into a laptop mic - 1,136 recordings through the
real pipeline, scored on whether the right thing was done.
- Whisper chooses between English, Hindi and Marathi only (Urdu counts as Hindi): left to itself it
  wrote a short "open chrome" as Vietnamese and Hindi in Urdu's script. A hint of typical commands
  and the user's names was tried and measured worse (in noise and in Hindi), so it isn't used.
- Commands are understood as they're said: numbers in words ("volume forty", "दस मिनट का टाइमर"),
  "seven in the morning", "six thirty", "गाना बंद करो", "H.U.D.", "that's all thanks".
- The wake word has a second opinion (`desktop/custodian_desktop/wake.py`): a small network trained
  on openWakeWord's own features from 200 synthetic voices. On voices it never heard it stops half
  the lookalikes ("Hey Travis" 13% -> 7%) and hears a few more real ones (86.5% -> 89%).
- The microphone is reopened when it goes quiet (the PC slept, a headset changed).
- Still hard: a TV or someone talking near the mic - Whisper hears their words too. A closer mic
  (a headset) helps most; voice ID keeps others' words from being acted on.

**Faster, more natural voice** (the desktop app).
- Replies are spoken sentence by sentence, the next one made while the last plays, so it starts
  talking after the first sentence; and transcription starts at the first pause in what you say.
- Barge-in: say the wake word while it's speaking to cut it off and ask something else (tray:
  *Interrupt by saying ...*).
- Hindi and Marathi: set the language in Jarvis → Settings (it goes to Whisper), and replies in
  Devanagari are spoken with a Hindi Piper voice (`run-desktop.ps1 -Hindi`) or Windows' Hindi voice.
- Your own wake word: train "Hey Custodian" with openWakeWord's
  [training notebook](https://github.com/dscripka/openWakeWord#training-new-models), put
  `hey_custodian.onnx` in `%APPDATA%\Custodian\wakewords`, and set `"wake_model": "hey_custodian",
  "wake_phrase": "Hey Custodian"` in `desktop.json`.
- Voice ID: `run-desktop.ps1 -VoiceId`, then tray → *Only my voice*, and say three phrases. Each
  spoken request is compared with your voiceprint on the PC before anything is sent - someone
  else's words aren't even transcribed - and with voice ID on, a request is refused if it can't
  be checked. It stops other people talking to your PC, not a recording of you: risky actions
  still ask.

**Deeper memory.** Memories are read for the people, places, organisations and projects they name
and how they relate, so "what does my sister do?" finds "Meera works at Infosys". A newer memory
that changes a fact ("moved from Pune to Mumbai") replaces the old one, which is kept as history.
Everything Custodian did is a searchable log ("what did I send Tanishka yesterday?") with undo
where it can: files back from the Recycle Bin, the volume, brightness or theme put back, moves
reversed.

**Your phone as a remote, and more integrations.**
- The Jarvis tab's remote: play/pause, next, volume, mute, dark mode, battery, lock the PC, or
  type any command. When you ask from the phone (or Telegram), a PC action that needs an OK is
  allowed **there**, for up to two minutes: allowed, it goes to the PC without a second dialog
  and the PC shows it was allowed remotely; denied or unanswered, it never reaches the PC. Turn
  *Allow PC actions from here* off to keep every approval on the PC.
- **Spotify** (`SPOTIFY_CLIENT_ID`/`_SECRET`): exact songs, albums, artists and playlists on any
  device with Spotify open (Premium), instead of clicking the app.
- **Microsoft 365** (`MICROSOFT_CLIENT_ID`/`_SECRET`): Outlook mail (read only) and calendar,
  with Teams links.
- **Telegram** (`TELEGRAM_BOT_TOKEN` from @BotFather): link your chat with a one-time code from
  the Jarvis tab; then chat (or send voice notes), allow PC actions with buttons, and get alerts.
  Any other chat is refused; groups are ignored; the bot polls, so no public address is needed.
- **Home Assistant**: paste its address and a long-lived access token in the Jarvis tab. Lights,
  switches, fans, AC (temperature and mode), scenes and media players - never locks, alarms,
  doors or covers. Its tools may reach that one address on your network and nothing else.

Each integration's tools have their own switches in Tools & Data, all off until you turn them on,
and each appears under Account → Where your data goes once connected.

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
checks pass them. The conversations cover memory, calendar, notes, reminders and files, plus
the PC and the web (at half the rate - with more, a model got worse at saving memories),
including multi-step tasks where each step uses the last one's result. The result is
installed in Ollama next to the model you use, never over it:

```powershell
cd backend; .\finetune\run_finetune.ps1    # an NVIDIA GPU (8 GB+) and Docker Desktop; ~3 h
```

Run it when the PC is idle (overnight): training needs almost the whole of an 8 GB GPU, and a
chat, video or game at the same time makes it many times slower. It then compares the new
model with yours (`finetune/compare.py`: held-out conversations, the accuracy and memory
evals; `eval/agent_eval.py`: 1000 everyday requests) and prints the command to switch if it's
better. Details: [backend/finetune/README.md](backend/finetune/README.md).

The model in use is `custodian-nemotron` (v4). Later rounds, each compared before switching:

| | Everyday (1000) | Held-out | Memory / new-chat recall | Kept? |
|---|---|---|---|---|
| v4 | 94.4% | 116/133 | 10/10 · 10/10 | **yes** |
| v5 (PC and web at half the data) | 89.2%* | 87/111 | 10/10 · 10/10, but saved memories less often | no |
| v6 (rebalanced) | 95.4% | 114/133 | 10/10 · 10/10 | no: questions about uploaded files fell from 4/6 to 1/6 (it checked the calendar instead) |

\* before the fast path and the routing fixes. v6 stays installed as `custodian-nemotron-v6`;
try it with `ollama cp custodian-nemotron custodian-nemotron-previous; ollama cp custodian-nemotron-v6 custodian-nemotron`.

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
| `WHISPER_URL` / `WHISPER_API_KEY` | (unset) | `local` (Whisper in the server) or an OpenAI-compatible speech-to-text server for voice messages |
| `WHISPER_LOCAL_MODEL` / `WHISPER_DEVICE` / `WHISPER_IDLE_MINUTES` | `large-v3-turbo` on a GPU, else `small` / `auto` / `10` | The local Whisper model, where it runs, and when it's let go |
| `VISION_MODEL` / `VISION_BASE_URL` / `VISION_API_KEY` | (unset) / `NEBIUS_BASE_URL` | Vision model that reads uploaded photos, e.g. `qwen3-vl:4b` |
| `VISION_KEEP_ALIVE` | `2m` | How long a vision model on this machine stays loaded after a use |
| `BACKUP_DIR` / `BACKUP_TIME` / `BACKUP_KEEP` | `data/backups` / `03:30` / `14` | Where the daily encrypted backup goes (another drive or a synced folder is best), when (`off` for only by hand), and how many are kept. Each is one file, encrypted with `APP_SECRET_KEY`: keep a copy of that key away from this PC |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | (unset) | Your Google OAuth client, for Calendar, Gmail and Drive |
| `LARGE_MODEL_ID` / `LARGE_MODEL_BASE_URL` / `LARGE_MODEL_API_KEY` | (unset) / Token Factory / `NEBIUS_API_KEY` | The larger model for **Think harder**, questions about the world, and escalation |
| `SCREEN_VISION_MODEL` | `VISION_MODEL` | The vision model that reads the screen for `pc_look` and `pc_click_on`, e.g. `qwen2.5vl:7b` |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` / `SPOTIFY_REDIRECT_URI` | (unset) | Your Spotify app, for the Spotify tools |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` / `MICROSOFT_REDIRECT_URI` | (unset) | Your Microsoft Entra app, for Outlook mail and calendar |
| `TELEGRAM_BOT_TOKEN` | (unset) | Your Telegram bot, for chatting, approvals and alerts on Telegram |
| `SEARXNG_URL` | `http://127.0.0.1:8888` | The SearXNG server for web search |
| `MODEL_KEEP_WARM_MINUTES` | `60` | Keep a local (Ollama) model loaded this long after the last message, so replies don't start with a 5 s load; `0` frees the GPU right away |
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
  it, "remembering" a secret or an off-the-record chat, citing a memory number that doesn't
  exist, or claiming something was done on your PC that no PC action did, all fail the check.
- **Sources must be real.** A "Source:" link that no web search or page read returned in that
  turn (and that you didn't give) is removed from the reply before you see it.
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
- **The web and your PC.** A tool reaches the network only if it is declared to: Google
  tools reach Google's API hosts, `web_search` reaches SearXNG, and `web_read_page` reaches
  public internet addresses only (DNS answers for loopback, private and link-local addresses
  are refused, so a web page can't be used to reach your router or this server). PC actions
  run in the desktop app, not on the server, and only for the signed-in owner. Risky actions
  need **Allow once** on the PC. There is no "run any command" tool. Web pages, files and
  emails are information, never instructions, and the accuracy check flags a reply that
  claims a PC action no tool carried out.
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
| `GET /features` | What this server has set up (voice, photos, larger model, Google, phone push, desktop app), unread count |
| `GET /desktop/next` · `POST /desktop/results/{id}` · `GET /desktop/status` | The desktop app: wait for the next PC action, report its result; is it connected |
| `POST /warm` | Start loading the local model now (the desktop app calls it on “Hey Jarvis”, the app when you start typing) |
| `GET /notifications` · `POST /notifications/{id}/read` · `POST /notifications/read-all` | The inbox |
| `GET /reminders` · `DELETE /reminders/{id}` | Upcoming reminders; cancel one |
| `GET/POST /documents` · `DELETE /documents/{id}` | Files: upload `{name, content_type, data}` (base64, up to 10 MB) |
| `POST /voice/transcribe` | Raw audio body → `{text}` |
| `POST/DELETE /messages/{id}/feedback` | Rate a reply: `{rating: 1 or -1, correction?, share}` |
| `GET /integrations` · `GET /integrations/google/start` · `DELETE /integrations/google` | Google connection |
| `GET /integrations/{spotify,microsoft}/start` · `DELETE /integrations/{provider}` | Spotify and Microsoft 365 connections |
| `POST /integrations/homeassistant` · `POST /integrations/telegram/link` | Home Assistant `{url, token}`; a Telegram link code |
| `GET /approvals` · `POST /approvals/{id}` | PC actions asked for from the app or Telegram, waiting for `{allow}` |
| `POST /remote` | `{command}`: one command for the PC from the phone |
| `GET/PATCH /settings/prefs` | Proactive, learning, remote-approval and voice-language settings |
| `GET/POST /routines` · `DELETE /routines/{id}` · `POST /routines/{id}/run` | Routines |
| `GET /activity?q=&days=` · `POST /activity/{id}/undo` | What Custodian did; undo one |
| `GET /learning` · `GET /learning/data` · `POST /learning/result` | Overnight learning: due?, its data, its outcome |
| `POST /briefing` · `GET /watches` · `DELETE /watches/{id}` · `GET /memory/graph` | Morning briefing, watchers, the memory map |
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
python eval/agent_eval.py            # 1000 everyday PC and web requests through the real model -> eval/agent_results.md
python eval/agent_eval.py --suite chrome   # 1000 browsing requests;  --suite pc: 1000 apps/settings/files/windows/messages

cd ../mobile
npx tsc --noEmit && npx expo export --platform web

cd ../desktop                        # Windows
.venv\Scripts\python -m pytest       # actions, confirmations, recording, voice, voice ID (nothing is clicked or typed)
$env:CUSTODIAN_TEST_BROWSER = "C:\Program Files\Google\Chrome\Application\chrome.exe"
.venv\Scripts\python -m pytest tests\test_browser.py   # the browser tools against a real, headless Chrome
```

Current results are in [backend/eval/results.md](backend/eval/results.md). CI
(`.github/workflows/ci.yml`) runs all of the above (except the agent tests, which need the
real model) and smoke-tests the Docker image.

The agent tests, with `custodian-nemotron` (v4) on an RTX 5050 laptop, 1000 requests each, worded
differently from the fine-tuning data. They go through the real model and the whole agent loop;
the desktop app is stood in by one that records what it was asked to do:

| Suite | Passed | Median | p95 | Report |
|---|---|---|---|---|
| Everyday mix (apps, sites, music, settings, files, WhatsApp, screen, web, multi-step) | 94.4% | 1.93 s | 6.80 s | [agent_results.md](backend/eval/agent_results.md) |
| Chrome (URLs, site searches, search-then-open, YouTube, reading pages, tabs) | 91.6% | 0.24 s | 6.06 s | [agent_results_chrome.md](backend/eval/agent_results_chrome.md) |
| PC operations (apps, windows, media, settings, files, keys, clipboard, messages) | 95.8% | 1.33 s | 2.58 s | [agent_results_pc.md](backend/eval/agent_results_pc.md) |

Run with the PC in use: on an idle PC the everyday median was 1.6 s and p95 3.1 s. Exact
everyday commands take ~0.1-0.3 s through `app/fastpath.py`; reading a live web page takes the
longest (5-7 s).
