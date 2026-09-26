# Custodian backend

Step 1 skeleton from `docs/PLAN.md` — `/chat` currently echoes the message back.
Each later step in the plan replaces or extends one file at a time; see
`docs/PLAN.md` for the full sequence and what each step adds.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Health check: `curl http://localhost:8000/health`
