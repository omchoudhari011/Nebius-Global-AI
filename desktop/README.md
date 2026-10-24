# Custodian for the desktop

A Windows tray app that turns Custodian into a voice assistant for your PC. Say **"Hey Jarvis"**
(or press **Ctrl+Alt+Space**), ask, and hear the answer. Custodian can act on the PC while
it answers: open apps and websites, control music and volume, manage windows and files,
type, and take screenshots.

```powershell
.\run-desktop.ps1             # first run: creates .venv, downloads the Piper voice, asks you to sign in
.\run-desktop.ps1 -Install    # start it automatically when you sign in to Windows
.\run-desktop.ps1 -Uninstall  # stop starting it automatically
.\run-desktop.ps1 -BrowserAccess   # once: let it read and click web pages in Chrome and Edge
```

You need:
- Python 3.11+.
- A running Custodian server (`..\run-local.ps1`).
- For voice, the server's `WHISPER_URL` set (see the main README).
- In **Tools & Data**, switch on **PC · read** and **PC · write**.

## Jarvis

Start everything at once with `..\run-jarvis.ps1` (Docker's speech-to-text and search, Ollama,
the server, then this app; `-Stop` stops the server and the app). Then:

- **The HUD.** A J.A.R.V.I.S. panel opens in the corner when you speak: an arc-reactor core that
  follows your voice, what you said and the reply, and the PC's vitals. **Ctrl+Alt+J**, the tray,
  or saying "show the HUD" opens it full screen: processor, memory, graphics, battery, drives and
  network with the busiest apps; today's schedule, reminders and weather; and what Custodian just
  did. It's `web/hud.html` in its own small window process (`hud_window.py`, pywebview with
  Windows' built-in WebView2); without pywebview the small text panel is used instead.
- **The manner.** Spoken requests are answered as Jarvis: calm, brief, a British voice
  (`en_GB-alan-medium`), and calling you "sir" - or what you set in the app's Jarvis tab. Only
  the manner changes; the replies go through the same checks. Typed chats keep Custodian's voice.
- **Conversation mode.** After a reply it listens a few more seconds without the wake word, so
  you can follow up. "That's all", "thank you" or saying nothing ends it.
- **Status reports.** "System status", "run diagnostics" or "how's my PC" gets an instant report.
  On its own it warns about a low battery (20%, then 10%), the processor or memory staying
  overloaded for two minutes, or a nearly full drive - once each, never in your quiet hours.
- **A greeting** when it starts ("Good evening, sir. Jarvis is online...") with your next
  appointment, except when the morning briefing is about to run or it's within your quiet hours.

The weather comes from Open-Meteo (no account): only your city's name and coordinates are sent.
The city is the Jarvis tab's setting, or the one your memories say you live in.

## How it works

| Part | File | What it does |
| --- | --- | --- |
| Bridge | `__main__.py` | Long-polls `GET /desktop/next` for the PC action the model chose, runs it and posts the result to `/desktop/results/{id}`. The app opens no port; the server only sees results. |
| Actions | `actions.py` | The 38 `pc_*` functions. Paths may start with `~`. Deleting moves to the Recycle Bin. The protected folders (`C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`, `C:\ProgramData`), your home folder and whole drives are never deleted. |
| Confirmations | `ui.py` | Risky actions show what will happen and wait for **Allow once**; after a minute with no answer, they are declined. The model is told "The user declined this on the PC." Every decision goes to `%APPDATA%\Custodian\desktop.log`. |
| Voice | `voice.py` | openWakeWord listens for the wake word ("Hey Jarvis", or your own model) on the PC. After it, the app records until about a second of quiet - transcription already starts at the first pause - sends the audio to `/voice/transcribe`, then sends the text to `/chat`. Turns within 15 minutes continue the same chat. Replies are spoken sentence by sentence by Piper (a Hindi voice for Devanagari), or by Windows' voice if Piper isn't installed; saying the wake word cuts a reply off. Links and source lists are not read out. |
| Voice ID | `voiceid.py` | With "Only my voice" on, each request is compared with your voiceprint (a WeSpeaker model run by onnxruntime, on the PC) before it's transcribed; someone else's is refused. Fails closed. |
| Browser | `browser.py` | Custodian's own Chrome or Edge, driven over the DevTools protocol (127.0.0.1 only, its own profile): read pages with numbered elements, fill, click, download, tabs. Risky clicks and sends ask; passwords are never typed. |
| HUD | `hud.py`, `hud_window.py`, `web/hud.html` | The HUD's own process, fed one JSON message per line (state, voice level, captions, vitals, your day); what you press in it comes back the same way. |
| Jarvis | `jarvis.py`, `watchdog.py`, `stats.py`, `weather.py` | The greeting and sign-offs, the vitals (one sampler shared by the HUD, the status report and the watchdog; `nvidia-smi` for the graphics card, read on the side), the watchdog's rules, and the weather. |
| Learning | `learning.py` | At night, idle and plugged in, runs `backend\finetune\nightly.py` when the server has new corrections to learn; stops it the moment you're back. |
| Settings | `config.py` | Settings in `%APPDATA%\Custodian\desktop.json`. The sign-in token is kept in Windows Credential Manager. |

Actions that ask first:
- `send_message` (WhatsApp), `type_text`, `press_keys`, `click`, `click_element`, `fill_field`, `write_in_app`
- `close_window`, `close_app`
- `read_file`, `write_file`, `move_file`, `delete_file`, `organize_folder`
- `get_clipboard`, `notifications` (Notification Centre's messages and the unread counts, read from a copy of Windows' list in `notices.py`)
- `screenshot`, `look` and `click_on` (the screen, read by the server's vision model; one OK covers look, click and check)
- `browser_download`; `browser_click` and `browser_fill` only for a submit, buy, pay, order, book, send or delete

Actions that just run (they only need their switch):
- `system_info`, `list_files`, `find_files`, `list_windows`, `list_apps`, `now_playing`, `read_screen`
- `write_document` (a new Word file in Documents, never over another; python-docx)
- `open`, `go_to`, `search_site`, `play`, `media`, `set_volume`, `focus_window`, `lock_screen`
- `set_brightness`, `set_theme`, `open_settings`, `timer`
- `copy_file`, `create_folder`, `set_clipboard`
- `browser_open`, `browser_read`, `browser_tabs`, and ordinary `browser_click` / `browser_fill`

An action you allowed in the phone app or on Telegram arrives already allowed: the PC shows
"Allowed from your phone" instead of asking again (WhatsApp still checks the chat's real name).

Some details:
- `play` finds the first YouTube video for the words and opens it, so it starts playing. On
  Spotify it opens the search and presses the top result's Play button.
- `go_to` opens DuckDuckGo's first result for a description.
- `read_screen`, `click_element` and `fill_field` use Windows UI Automation: control names,
  not pixels.
- `send_message` opens the WhatsApp chat by name and reads whose chat it is from WhatsApp's own message
  box ("Type a message to Tanishka"). A different or missing chat means nothing is sent. Its dialog
  shows that real name and the exact text, and it sends only on **Allow once**.
- A window or app is matched by what you mean: "WhatsApp" is the WhatsApp app, not a file
  called `whatsapp_bot.py` open in an editor. The dialog names the exact window first.
- For web pages in Chrome or Edge, run `.\run-desktop.ps1 -BrowserAccess` once and restart the
  browser (`-BrowserAccess:$false` undoes it).

There is no tool that runs arbitrary commands.

## Settings

Edit `desktop.json` while the app is closed:

| Key | Default | |
| --- | --- | --- |
| `server` | `http://localhost:8001` | The Custodian server |
| `wake_word` / `wake_threshold` | `true` / `0.5` | Listen for the wake word. Raise the threshold if it wakes by mistake. |
| `wake_model` / `wake_phrase` | `hey_jarvis` / `Hey Jarvis` | Your own openWakeWord model: its name in `%APPDATA%\Custodian\wakewords` (e.g. `hey_custodian` for `hey_custodian.onnx`), and what to tell you to say |
| `barge_in` | `true` | Saying the wake word while it speaks cuts it off (also in the tray) |
| `fast_transcribe` | `true` | Start transcribing at the first pause |
| `hindi_voice` | `hi_IN-pratham-medium` | The Piper voice for Hindi and Marathi replies (`run-desktop.ps1 -Hindi`) |
| `voice_id` / `voice_id_threshold` | `false` / `0.45` | Only your voice (`run-desktop.ps1 -VoiceId`, then the tray); a higher threshold is stricter |
| `hotkey` | `<ctrl>+<alt>+<space>` | Talk without the wake word (pynput format) |
| `speak_replies` | `true` | Also in the tray menu |
| `voice` | `en_GB-alan-medium` | A Piper voice in `%APPDATA%\Custodian\voices` (falls back to `en_US-lessac-medium`, then Windows' voice) |
| `conversation_mode` / `follow_up_seconds` | `true` / `6` | Listen for a follow-up after each reply (also in the tray) |
| `hud` / `hud_hotkey` | `true` / `<ctrl>+<alt>+j` | The HUD, and the key for its full-screen view |
| `watchdog` | `true` | Speak up about the battery, an overloaded PC or a full drive |
| `greet` | `true` | Say hello when the app starts |
| `microphone` | `null` | A sounddevice input number; `null` = the default |
| `protected` | the four folders above | Folders that are never changed |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

The tests cover files, protected folders, confirmations (including a real dialog timing out),
approvals from the phone, speech cleaning and recording, speaking sentence by sentence, barge-in,
early transcription, voice ID, the screen's capture-and-click, and when overnight learning starts
and stops. They never type, click or speak. `tests\test_browser.py` drives a real, headless
Chrome when `CUSTODIAN_TEST_BROWSER` names it.
