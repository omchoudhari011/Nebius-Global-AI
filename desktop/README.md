# Custodian for the desktop

A Windows tray app that turns Custodian into a voice assistant for your PC. Say **"Hey Jarvis"**
(or press **Ctrl+Alt+Space**), ask, and hear the answer. Custodian can act on the PC while
it answers: open apps and websites, control music and volume, manage windows and files,
type, and take screenshots.

```powershell
.\run-desktop.ps1             # first run: creates .venv, downloads the Piper voice, asks you to sign in
.\run-desktop.ps1 -Install    # start it automatically when you sign in to Windows
.\run-desktop.ps1 -Uninstall  # stop starting it automatically
```

You need:
- Python 3.11+.
- A running Custodian server (`..\run-local.ps1`).
- For voice, the server's `WHISPER_URL` set (see the main README).
- In **Tools & Data**, switch on **PC · read** and **PC · write**.

## How it works

| Part | File | What it does |
| --- | --- | --- |
| Bridge | `__main__.py` | Long-polls `GET /desktop/next` for the PC action the model chose, runs it and posts the result to `/desktop/results/{id}`. The app opens no port; the server only sees results. |
| Actions | `actions.py` | The 20 `pc_*` functions. Paths may start with `~`. Deleting moves to the Recycle Bin. The protected folders (`C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`, `C:\ProgramData`), your home folder and whole drives are never deleted. |
| Confirmations | `ui.py` | Risky actions show what will happen and wait for **Allow once**; after a minute with no answer, they are declined. The model is told "The user declined this on the PC." Every decision goes to `%APPDATA%\Custodian\desktop.log`. |
| Voice | `voice.py` | openWakeWord listens for "Hey Jarvis" on the PC. After it, the app records until about a second of quiet, sends the audio to `/voice/transcribe`, then sends the text to `/chat`. Turns within 15 minutes continue the same chat. Replies are spoken by Piper, or by Windows' voice if Piper isn't installed. Links and source lists are not read out. |
| Settings | `config.py` | Settings in `%APPDATA%\Custodian\desktop.json`. The sign-in token is kept in Windows Credential Manager. |

Actions that ask first:
- `type_text`, `press_keys`, `click`
- `close_window`
- `read_file`, `write_file`, `move_file`, `delete_file`
- `get_clipboard`
- `screenshot`

Actions that just run (they only need their switch):
- `system_info`, `list_files`, `find_files`, `list_windows`
- `open`, `media`, `set_volume`, `focus_window`, `lock_screen`
- `set_clipboard`

There is no tool that runs arbitrary commands.

## Settings

Edit `desktop.json` while the app is closed:

| Key | Default | |
| --- | --- | --- |
| `server` | `http://localhost:8001` | The Custodian server |
| `wake_word` / `wake_threshold` | `true` / `0.5` | Listen for "Hey Jarvis". Raise the threshold if it wakes by mistake. |
| `hotkey` | `<ctrl>+<alt>+<space>` | Talk without the wake word (pynput format) |
| `speak_replies` | `true` | Also in the tray menu |
| `voice` | `en_US-lessac-medium` | A Piper voice in `%APPDATA%\Custodian\voices` |
| `microphone` | `null` | A sounddevice input number; `null` = the default |
| `protected` | the four folders above | Folders that are never changed |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

The tests cover files, protected folders, confirmations (including a real dialog timing out),
speech cleaning and recording. They never type, click or speak.
