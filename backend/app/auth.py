"""Single-user session cookie, checked on every route except /health.
Signed with itsdangerous.TimestampSigner; a FastAPI Depends(require_session)
raises 401 if missing or invalid. CORS is already set up in main.py
(CORS_ORIGINS, with allow_credentials on for this cookie). Step 4 — see docs/PLAN.md.
"""


def require_session() -> None:
    raise NotImplementedError("Step 4")
