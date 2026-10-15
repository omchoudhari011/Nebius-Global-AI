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
the teacher's reply unchanged and the turn did what was expected. All data is synthetic:
none of your chats are used.

| Behaviour | What the model learns |
|---|---|
| introduction, remember_fact | Call `remember`, in the user's own words; greet by name |
| name_recall, fact_recall, earlier_chat | Answer from saved memories and quoted earlier messages |
| dont_know | Say "I don't know" when nothing is on file |
| secret, off_the_record | Never save secrets, or anything from an off-the-record chat |
| calendar_read, note_read, daily_summary | Call the tool first; say plainly when a switch is off |
| calendar_create, note_create, reminder | Only claim what succeeded; no reminders exist, so use the calendar |
| calendar_ask_time | Ask for a missing time instead of guessing |
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
