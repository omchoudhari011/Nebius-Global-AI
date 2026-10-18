# Fine-tuning the model for Custodian

Small local models make the same few mistakes in Custodian: they don't save what you tell
them (so a new chat doesn't know your name), they describe a calendar they couldn't read,
and they promise reminders the app can't send. The accuracy checks catch the false claims at
run time; fine-tuning teaches the model not to make them in the first place.

```powershell
cd backend
.\finetune\run_finetune.ps1        # dataset -> training -> GGUF -> Ollama -> comparison
```

Then set `NEBIUS_MODEL_ID=custodian-nemotron` in `backend/.env` and restart Custodian.

## What it trains on

`make_dataset.py` runs scenarios through the real agent loop with a scripted "teacher" in
place of the model, so every example has the exact production prompt, tools, permission
denials and tool results. A conversation is kept only if the app's own accuracy checks pass
the teacher's reply unchanged and the turn did what was expected. The synthetic data uses
none of your chats; replies you rate in the app and mark "use this to improve the model" are
added by `from_feedback.py` (each three times), and nothing else of yours is.

| Behaviour | What the model learns |
|---|---|
| introduction, remember_fact | Call `remember`, in the user's own words; greet by name |
| name_recall, fact_recall, earlier_chat | Answer from saved memories and quoted earlier messages |
| dont_know | Say "I don't know" when nothing is on file |
| secret, off_the_record | Never save secrets, or anything from an off-the-record chat |
| calendar_read, note_read, reminder_list | Call the tool first; say plainly when a switch is off |
| daily_summary | Read the calendar, notes and reminders together, then summarise |
| calendar_create, note_create, reminder | Only claim what succeeded; reminders (one-off or repeating) go through the reminders tool |
| calendar_ask_time, reminder_ask_time | Ask for a missing time instead of guessing |
| document | Search the uploaded files when asked about "my lease", "the warranty"..., and answer from them |
| contact | It can't text, email or call: say so and offer a reminder |
| injection | Ignore instructions inside quoted text |
| general | Answer plain questions directly, without tools |

Loss is computed on the assistant turns only (its tool calls and replies).

## Measured

`compare.py` gives the model the same prompts as the held-out conversations (never trained
on) and checks its first step, then runs the app's hallucination and recall evals. The base
`nemotron-3-nano:4b` scored **31/42**: 0/4 facts saved, 1/4 introductions saved, 0/2 daily
summaries that read the notes, and it called `remember` in an off-the-record chat where the
tool wasn't even offered. Results land in `finetune/out/compare.md`.

After fine-tuning (2026-10-15, 374 conversations, best checkpoint at validation loss 0.49 vs
0.79 untrained), measured the same way on an RTX 5050 Laptop:

| | `nemotron-3-nano:4b` | `custodian-nemotron` |
|---|---|---|
| Held-out first step right | 31/42 | **38/42** |
| Facts saved when asked ("remember that ...") | 0/4 | **4/4** |
| False first drafts (hallucination guard, 10 tempting prompts) | 2/10 | **0/10** |
| Memory recall / new-chat recall | 10/10 / 9/10 | 10/10 / **10/10** |

Its 4 misses: 2 daily summaries that read the calendar but not the notes, 1 introduction not
saved (the server's name backstop saves it anyway), and 1 `remember` call in an off-the-record
chat (the server refuses it).

**Second round (v2, 2026-10-16)** added reminders, file questions and the three-part daily
summary (515 conversations; stopped at the best checkpoint, step 70 of 116, validation loss
0.48). On the new held-out set it beat v1, 50/53 to 48/53, fixing earlier-chat recall, an
injection and "text me" requests. But on the independent hallucination prompts it made 1
false first draft out of 10 where v1 made 0: it answered an ordinary "remember that I'm moving
to Pune" with "this chat is off the record", because that exact sentence appeared only in
off-the-record examples. So `custodian-nemotron` (v1) stays the active model and v2 is kept as
`custodian-nemotron-v2`. The dataset now pairs every off-the-record example with the same
words in a normal chat that saves them, so the next run learns the rule, not the wording.

**Third round (v3, 2026-10-16) - the active model.** Same data as v2 plus those pairs (536
conversations), 1.5 epochs, best validation loss 0.435 (v1 0.49, v2 0.48). Against v1:

| | v1 | **v3** |
|---|---|---|
| Held-out first step right (55 conversations) | 49/55 | **52/55** |
| False first drafts, hallucination prompts (2 runs of 10) | 1, 0 | **0, 0** |
| Every tool asked for in plain words (calendar, notes, reminders, files, memory, daily summary) | - | **10/10** |

v3 now searches your files when asked about "my lease", handles the injection and the
introduction v1 missed, and reads calendar, notes and reminders for the daily summary. Its 3
misses: a `remember` call in an off-the-record chat (the server refuses it), an extra notes
search before answering from an earlier chat, and offering a reminder when asked to text.
Ollama keeps `custodian-nemotron-v1` and `-v2`; `custodian-nemotron` is v3.

## Requirements and notes

- An NVIDIA GPU with 8 GB or more, Docker Desktop with WSL 2 GPU support, Ollama.
- The model is a hybrid Mamba-Transformer whose kernels have no Windows build, so training
  runs in a Linux container (`Dockerfile`). Only mamba-ssm's Triton kernels are used; they
  compile themselves for the GPU present, including RTX 50-series cards.
- `--qlora` loads the base model in 4 bits so it fits in 8 GB; LoRA rank 16 on the attention
  and MLP layers, 2 epochs, learning rate 5e-5, gradients clipped at 0.3.
- The Mamba mixers are left frozen. Adapting their `in_proj` (whose output goes through an
  exponential) and `out_proj` made gradient norms spike past 1,000,000; in one run the
  validation loss went from 0.79 untrained to 0.66 after epoch 1, then to 1.79 after epoch 2.
- Validation loss is checked every quarter epoch and the best checkpoint is the one saved, so
  a run that turns unstable late still yields its best model, never its last.
- On an 8 GB GPU two things are needed and done for you: the Mamba scan runs on mamba-ssm's
  Triton kernel (the PyTorch fallback builds a 20 GB tensor), and the loss projects onto the
  131k-token vocabulary only at the ~3% of positions being trained. One step of 8
  conversations takes about 30 s on an RTX 5050 Laptop, so a 2-epoch run is about 50 minutes.
- The merge streams one tensor at a time, so it fits in Docker Desktop's default memory.
- Base model: [nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16),
  under the NVIDIA Open Model License; the fine-tuned model is a derivative under the same terms.

| File | Runs | Does |
|---|---|---|
| `make_dataset.py` | here | Builds `data/train.jsonl` and `data/valid.jsonl` |
| `train_lora.py` | container (`--check` runs here) | LoRA training -> `out/adapter` |
| `export_gguf.py` | container | Merge, GGUF and 4-bit quantization -> `out/custodian-nemotron-Q4_K_M.gguf` |
| `install_ollama.py` | here | `ollama create custodian-nemotron` with the base model's template |
| `compare.py` | here | Base vs fine-tuned -> `out/compare.md` |

## Learning from the big model (distill.py)

`make_dataset.py`'s conversations come from templates, so the local model learns those phrasings
and slips on new ones. `distill.py` keeps the same synthetic world and expectations but has the
larger model (`LARGE_MODEL_ID`, default `nvidia/nemotron-3-super-120b-a12b` on Token Factory)
rewrite each message eight ways people really talk - Hinglish, typos, voice-transcription,
indirect, very short, polite and long - and drops a rewrite unless every name, number, time and
quoted text, and at least half of its other content words, survive. Each rewrite then runs through
`orchestrator.handle()` with the larger model answering (`--replies scripted` keeps the usual
answers instead) and is kept only if the accuracy checks pass it unchanged and it called every tool
the scenario plans.

```powershell
$env:NEBIUS_API_KEY = "..."
python finetune\distill.py --per-kind 16 --variants 6     # -> data\distilled.jsonl (+ distilled_valid.jsonl)
python finetune\compare.py --valid finetune\data\distilled_valid.jsonl   # how a model copes with new phrasings
```

`train_lora.py` adds `data/distilled.jsonl` to the training set when it exists (`--no-distilled`
leaves it out); `run_finetune.ps1 -Distill` runs it as part of the usual pipeline.

## Learning overnight (nightly.py)

The desktop app runs `nightly.py` between 01:00 and 06:00 when the PC is idle and plugged in and
the server has at least three new shared ratings (`GET /learning`). It downloads them
(`GET /learning/data`: the rated replies, and each correction as a preference pair), continues the
adapter in `out/adapter` for one epoch on them plus `--replay` usual conversations, with DPO on the
pairs (`train_lora.py --init-adapter --pairs`; the reference is the adapter as it was before the
night), exports and installs it as `custodian-nemotron-next`, compares, and switches only if
`decide()` finds nothing worse. The old model stays as `custodian-nemotron-previous` and the old
adapter as `out/adapter-previous`. The outcome goes to `POST /learning/result`, which leaves a note
in the app's inbox. Stopping it (coming back to the PC) leaves the ratings for the next night.
