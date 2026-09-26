"""FastAPI app. Step 1 — see docs/PLAN.md.

/chat is a stub that echoes the message back; Step 2 replaces the handler
body with a real call into app.orchestrator.handle(), and Step 4 adds the
auth dependency and CORS lockdown. Nothing about this route's shape changes
after today — that's the point of building the frame first.
"""

from fastapi import FastAPI
from pydantic import BaseModel

from .db import init_db

app = FastAPI(title="Custodian")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    used: dict


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # Step 1 stub — swap this body for app.orchestrator.handle() in Step 2.
    return ChatResponse(reply=f"echo: {req.message}", used={})
