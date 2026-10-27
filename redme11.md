You are working on Custodian, my personal AI assistant. Read this whole brief before doing anything. If anything here conflicts with how the code actually works, tell me instead of forcing it.

## What exists today (must keep working)
- backend/: the server. NVIDIA Nemotron via Nebius, lifelong memory with meaning-based search, tools, scheduled jobs, safety checks, encrypted storage. Runs on port 8001.
- mobile/: chat app for iOS, Android and web.
- desktop/: "Jarvis", a Windows tray app with a "Hey Jarvis" wake word, spoken replies, an on-screen HUD and PC control (apps, volume, music, documents, reading the screen and clicking, driving Chrome/Edge, routines).
- store/: Pro and Cloud plans through Stripe with offline license keys. We sell this, so the license of everything we add matters.
- Installer: Custodian Setup.exe, install.ps1, update.ps1.
- voice-lab/: 1,136 recordings, 10 voices, 4 conditions.
- Languages: English, Hindi and Marathi, spoken and typed.
After every phase, the server, the "Hey Jarvis" listener, web search and all existing features must still work.

## Goal
Make Jarvis reliably operate the whole Windows PC, make its English/Hindi/Marathi understanding measurably better, and build a data pipeline so it keeps improving. Target architecture:
1. Command + screen state go to a planner (Nemotron + saved skills).
2. An action router tries the cheapest reliable path first:
   a. Native APIs: PowerShell from an allowlist, COM for Office, URI schemes like ms-settings:, Windows MCP agent connectors where available.
   b. Windows UI Automation: act on named controls through UIA patterns (Invoke, Value, Toggle, ExpandCollapse, SelectionItem) before touching the mouse. Use what fits the existing code (pywinauto or uiautomation in Python, FlaUI in .NET).
   c. Vision fallback: a screenshot grounding model, only when UIA can't see the control.
3. Every action is verified against an explicit postcondition. Retry or replan at most 3 times, then ask me.
4. Passed runs become reusable skills. Every step is logged as a trajectory for evals and training.

## Rules for how you work
- Start with Phase 0. Do one phase at a time, each on its own git branch with small, clear commits. Don't force-push or rewrite history, and don't refactor code unrelated to the current phase.
- At the end of each phase, stop and report: files changed, how to run it, before/after metrics, known issues, next steps. Wait for my "go" before starting the next phase.
- Only report numbers you actually measured. If you couldn't run something, say so.
- Never test destructive actions on my real files or settings. Use a dedicated folder (e.g. %TEMP%\custodian-eval), Windows Sandbox if this Windows edition has it, or a VM. Restore any setting a test changes. Ask before installing anything system-wide.
- Before adding a dependency or model, check its current version and license yourself rather than from memory. MIT, Apache and BSD are fine; flag GPL, AGPL or non-commercial licenses to me first.
- Never commit secrets. Logs and screenshots stay on the device unless I opt in. Respect off-the-record mode.

## Phase 0: Recon (no code changes)
Explore the repo and write docs/ROADMAP.md mapping every phase below onto the real files and modules. List your assumptions and ask me up to 5 questions.

## Phase 1: Measurement
1. Trajectory logger. For every PC action, save the instruction, a compressed UIA snapshot, a screenshot, the chosen action, the result, pass/fail and duration, as JSONL plus images, stored locally with a retention setting.
2. PC task suite. Start with 30 Windows tasks (aim for 50), each with a setup script and an automatic checker (file exists or contains X, window title, setting value, document text). The runner reports success rate, steps and time per task.
3. Language golden set. A few hundred utterances across English, Hindi and Marathi: Roman script, Devanagari, code-mixed ("kal subah 7 baje alarm laga do", "udya 5 la reminder lav") and spoken (from voice-lab). Each has the expected tool call and arguments. The runner reports intent accuracy, argument accuracy and end-to-end success per language.
4. Hearing vs understanding. ASR word and character error rates on voice-lab, separately from intent accuracy on the correct transcripts.
5. Wake word. False wakes per hour on long background audio (TV, YouTube, people talking; ask me to record about 2 hours if none exists), plus false rejects.
Record all baselines in docs/EVALS.md.

## Phase 2: Action router, verification, safety
- Build the native-API rung, the UIA rung and the router. UIA observation: only the foreground window's visible, enabled, interactive controls, each with a short numeric ID, sent as a compact list. The model picks an ID, not pixels.
- Every action declares a postcondition and the loop verifies it.
- Make the app per-monitor DPI aware, handle multi-monitor coordinates, and confirm the target window has focus before typing.
- Risk tiers: read-only actions run freely; reversible ones run and log an undo; irreversible ones (delete, send, install, pay, shell commands outside the allowlist) need my confirmation.
- Kill switch: a global hotkey and "Jarvis, stop" halt the executor immediately.
- Treat text from screens, web pages, emails and PDFs as untrusted data, never as instructions.
- Screenshot privacy: a configurable list of windows (password managers, banking) that are skipped or blurred before anything leaves the PC.
- An action timeline in the HUD and app, with one-click undo where possible.
Re-run the Phase 1 suites and report the change.

## Phase 3: Skills and "watch me once"
- Verified successful runs become parameterized skills (e.g. "export {file} as PDF") that replay deterministically with verification. Call the LLM only if a replay fails. Match requests to skills by meaning.
- Recorder: I start and stop it explicitly, with a visible indicator. It captures my clicks and keys plus the UIA element under the cursor, never password fields, and saves a skill plus training data.

## Phase 4: Vision fallback
- Put grounding behind one interface: locate(screenshot, instruction) returns a box and a confidence.
- Benchmark candidates on our task suite: H Company's Holo3/Holo3.1 open models, and NVIDIA Nemotron 3 Nano Omni or Nemotron Nano 2 VL (the latter is on Nebius). Check each license for commercial use.
- Optionally, numbered-overlay (set-of-marks) screenshots for the planner.

## Phase 5: Language fixes
- No calendar math in the LLM. It outputs a structured time expression, and deterministic code resolves it in Asia/Kolkata time. Add unit tests for:
  - Hindi "kal" (yesterday or tomorrow) and "parson" (two days either way): resolve by tense; reminders are always in the future.
  - Fraction times: Hindi sava, saadhe, paune, dedh, dhai; Marathi savva, saade, paavne, deed, adich ("paune chhe" = 5:45, "adich" = 2:30).
  - Day parts: subah, dopahar, shaam, raat; sakali, dupari, sandhyakali, ratri.
- Normalize Roman, Devanagari and code-mixed input to one canonical form before understanding.
- Resolve entities from real indexes: installed apps (Get-StartApps plus Start Menu shortcuts), contacts and recent files, using fuzzy and phonetic matching, transliterating first ("वीएस कोड" → VS Code).
- Dialogue state, so "open it", "make it louder" and "send this" resolve to the last app, the last file or the current selection.
- If ASR is the bottleneck, A/B test AI4Bharat's IndicConformer (MIT) against the current ASR on voice-lab, and bias recognition toward my app and contact names.
- Wake word: add a second-stage check on the 1–2 second clip before waking, and speaker verification so only my voice can run risky actions.

## Phase 6: Training pipeline
- Export logs as datasets: grounding pairs auto-labelled from UIA (screenshot + control name → box), intent-routing pairs, and preference pairs from my corrections (original reply vs corrected one).
- QLoRA scripts for a small open vision model (2–4B, must fit a free Kaggle or Colab GPU) and a small intent-routing model. Evaluate before and after on our suites, and never ship a model that regresses.

## Backlog (plan only, don't build yet)
Phone-to-PC remote commands; semantic search over my local files; an offline mode with a small local model and local ASR; "this" awareness ("summarize this" acts on the current selection or open file).

Start with Phase 0 now.