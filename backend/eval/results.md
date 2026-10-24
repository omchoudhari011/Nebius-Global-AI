# Eval results

Written by the scripts in this folder - never hand-typed. Regenerate everything with
`python eval/run_all.py` (offline stub model) or `python eval/run_all.py --model nebius`.
Each script runs against a throwaway database, so your own data is never touched.

- Memory recall accuracy: 10/10 questions recalled the right memory (20 facts among 320 memories; about 79 fit in each prompt, best matches first) _(2026-10-24)_
- Lifetime chat recall: 10/10 questions asked in a new chat found the right earlier message (10 facts said in passing, never saved as memories, among 420 messages in 40 chats over 60 days) _(2026-10-24)_
- Paraphrase recall: 10/10 found with keyword + meaning search vs 0/10 with keywords alone (questions worded differently from facts said in passing, among 420 messages in 40 chats over 60 days; nomic-embed-text); small talk pulled in 0.2 earlier messages on average (0.2 with keywords alone) _(2026-10-24)_
- Latency p50 / p95: turn 347 / 585 ms; model 0 / 0 ms per turn; tool call 190 / 233 ms (8 sandboxed calls) - 10 turns, stub (stub-offline) _(2026-10-24)_
- Permission-block rate: 100% - 200/200 tool calls blocked with the scope revoked (200 logged as denied in tool_calls); 0/20 wrongly blocked with it granted _(2026-10-24)_
- Security-test pass rate: 27/27 (100%) - SQL injection in memories, path-traversal arguments, sandbox network/file/process/secret isolation; guardrails: secrets never saved or recalled, recalled text kept out of the system prompt, memory writes per reply capped, task frequency and daily runs limited _(2026-10-24)_
- Hallucination guard: 24/24 scripted false claims handled as expected (12 rewritten by the repair round, 12 flagged in the reply); 0/17 true replies wrongly flagged; 5/5 invented memories refused, 5/5 paraphrased ones kept _(2026-10-24)_
- Hallucination guard (real model): 2/10 first drafts made a claim the checks caught (2 fixed by the repair round, 0 flagged in the reply), on prompts that tempt a model to make things up (nebius (nemotron-3-nano:4b)) _(2026-10-15)_
