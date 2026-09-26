"""Single-user session cookie, checked on every route except /health.
Signed with itsdangerous.TimestampSigner; a FastAPI Depends(require_session)
raises 401 if missing or invalid. CORS locked to the Expo web origin here too.
Step 4 — see docs/PLAN.md.
"""


def require_session() -> None:
    raise NotImplementedError("Step 4")
