# Custodian backend

Step 1 skeleton from `docs/PLAN.md` — `/chat` currently echoes the message back.
Each later step in the plan replaces or extends one file at a time; see
`docs/PLAN.md` for the full sequence and what each step adds.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets a phone on the same Wi-Fi reach the API. Without it,
uvicorn listens on `127.0.0.1` and only accepts connections from this computer.
Until Step 4 adds the session check, anyone on that network can call the API,
so use it on a network you trust.

Health check: `curl http://localhost:8000/health`

The Expo web build (`npm run web` in `mobile/`) can call the API because
`CORS_ORIGINS` in `.env` allows `http://localhost:8081`. Add other origins,
comma-separated, if you open the web build from another address.
