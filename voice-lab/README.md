# Voice lab: testing how Custodian hears

Custodian is tested the way people use it: spoken requests for every kind of task, in many voices -
British and American, Indian English, Hindi - said normally, in a hurry, with someone talking and a
fan going, and from across the room into a laptop's microphone. Each recording goes through the
real pipeline (Whisper in the server, then the same routing the app uses) and is scored on what
matters: **was the right thing done**, not how the words were spelled.

```powershell
.\voice-lab\get_voices.ps1                                          # the Piper voices (~560 MB)
desktop\.venv\Scripts\python voice-lab\build.py voice-lab\clips     # 1,136 recordings (~1.5 min)
backend\.venv\Scripts\python voice-lab\route.py                     # routing from the exact words
backend\.venv\Scripts\python voice-lab\run.py voice-lab\clips results.json   # hear + route (~13 min, GPU)
backend\.venv\Scripts\python voice-lab\compare.py voice-lab\clips A B        # ways of hearing, side by side
desktop\.venv\Scripts\python voice-lab\wake.py                      # the wake word: wakes vs false wakes
desktop\.venv\Scripts\python voice-lab\wake_train.py out.npz         # train the wake word's second opinion
desktop\.venv\Scripts\python voice-lab\wake_vitrus_train.py out.npz  # train "Hey Vitrus" (copy to desktop/custodian_desktop/models/hey_vitrus.npz)
```

| File | What it is |
|---|---|
| `suite.py` | The requests (38 English, 6 Hindi), each with what it must lead to |
| `build.py` | Speaks them in 7 English and 3 Hindi voices, under four conditions |
| `route.py` | What Custodian does with a sentence: the PC's own commands, then the exact (fast) commands, then the model's skill |
| `run.py` | Whisper on each recording, then `route.py`; prints the score by condition, voice and language, and the worst requests |
| `compare.py` | The same recordings heard different ways (any language / three languages / a hint ...) |
| `wake.py` | "Hey Vitrus" and lookalikes streamed through the wake word, at each threshold |
| `wake_train.py` | Trains `desktop/custodian_desktop/models/hey_jarvis_plus.npz` and measures it on voices it never heard |
| `wake_vitrus_train.py` | Trains `desktop/custodian_desktop/models/hey_vitrus.npz`, the "Hey Vitrus" wake word, and measures wakes, lookalikes and false wakes on voices it never heard |

The voices are synthetic, so the numbers compare settings fairly but aren't a promise for a real
voice. The Hindi voices reading English are a harsher stand-in for an Indian accent than real
speech (they mangle some English words); a person's own recordings would be the truest test.
