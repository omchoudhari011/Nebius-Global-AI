"""FastAPI app. Step 1 — see docs/PLAN.md.

/chat is a stub that echoes the message back; Step 2 replaces the handler
body with a real call into app.orchestrator.handle(), and Step 4 adds the
auth dependency. CORS is set up below, so the Expo web build can call this API
from day one. Nothing about this route's shape changes after today — that's the
point of building the frame first.
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .db import init_db

load_dotenv()  # reads backend/.env (MODEL_BACKEND, APP_SECRET_KEY, CORS_ORIGINS, ...)

app = FastAPI(title="Custodian")

# The Expo web build runs on another origin (http://localhost:8081 by default), so the
# browser only lets it call this API if these CORS headers allow that origin. Phones
# running Expo Go don't use CORS. Add more origins, comma-separated, in CORS_ORIGINS.
# allow_credentials is on for the Step 4 session cookie, which is why origins must be
# listed explicitly and never "*".
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8081").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


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
