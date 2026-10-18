"""FastAPI app: the API, plus the exported web app when one is present.

Startup (lifespan): check settings, create, verify or upgrade the database, make sure the
account row exists, sync skill files into new versions, start embedding anything not yet
embedded for search by meaning, mark interrupted tool calls failed, and start the scheduler.
Every route except /health and /auth/login requires a session.

Run with one worker process: the scheduler lives in this process.
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, auth, db, licensing, memory, permissions, plans, privacy, scheduler, skills, tasks, vectors
from .config import BACKEND_DIR, Settings, get_settings
from .model_backends import ModelError, close_backend
from .orchestrator import ConversationNotFound, SkillUnavailable
from .routers import auth as auth_routes
from .routers import billing as billing_routes
from .routers import chat as chat_routes
from .routers import everyday as everyday_routes
from .routers import data as data_routes
from .routers import jarvis as jarvis_routes
from .routers import privacy as privacy_routes
from .routers import tasks as task_routes
from .tools import sandbox

log = logging.getLogger("custodian")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",  # the mic records voice messages
    "Cross-Origin-Opener-Policy": "same-origin",
}
# react-native-web injects <style> tags at runtime, so styles need 'unsafe-inline'.
HTML_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# The largest real request is a chat message (8,000 characters, under 50 KB as escaped JSON).
MAX_BODY_BYTES = 256 * 1024
# Uploads: a 10 MB file is ~13.4 MB in base64; a voice message is sent as raw audio.
LARGE_BODY_PATHS = {"/documents": 15 * 1024 * 1024, "/voice/transcribe": 11 * 1024 * 1024}
LARGE_BODY_PREFIXES = {"/desktop/results/": 12 * 1024 * 1024}  # screenshots come back as images


class BodySizeLimit:
    """Refuse request bodies over `max_bytes` with 413 before they are read into memory:
    by Content-Length up front, and by counting for chunked bodies that have none."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES) -> None:  # type: ignore[no-untyped-def]
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = LARGE_BODY_PATHS.get(path) or next(
            (size for prefix, size in LARGE_BODY_PREFIXES.items() if path.startswith(prefix)), self.max_bytes
        )
        for name, value in scope["headers"]:
            if name == b"content-length":
                if not value.isdigit() or int(value) > limit:
                    response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
                    return await response(scope, receive, send)
        received = 0

        async def limited_receive():  # type: ignore[no-untyped-def]
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, detail="Request body too large")
            return message

        return await self.app(scope, limited_receive, send)


def _is_cross_site_write(request: Request, settings: Settings) -> bool:
    """A state-changing request riding on the session cookie from a page on another site
    (CSRF). SameSite=lax already stops most of these; this also covers COOKIE_SAMESITE=none
    and sibling subdomains. Bearer-token clients (the native app) don't use the cookie."""
    if request.method not in UNSAFE_METHODS or auth.COOKIE_NAME not in request.cookies:
        return False
    if request.headers.get("Authorization", "")[:7].lower() == "bearer ":
        return False  # auth._token_from() uses the header, never the cookie, for this request
    origin = request.headers.get("Origin")
    if origin:
        if origin.rstrip("/") in settings.cors_origins:
            return False
        return urlsplit(origin).netloc.lower() != request.headers.get("Host", "").lower()
    return request.headers.get("Sec-Fetch-Site") == "cross-site"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    logging.getLogger("apscheduler").setLevel(max(logging.WARNING, logging.getLevelName(level)))
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _web_dir(settings: Settings) -> Path | None:
    candidate = settings.web_dist_dir or BACKEND_DIR / "web"
    return candidate if (candidate / "index.html").is_file() else None


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.init_db()
        auth.ensure_owner()
        for name, version in skills.sync_from_files():
            log.info("saved skill %s v%d", name, version)
        vectors.start_backfill()  # embeds existing memories and messages in the background
        if (stale := sandbox.recover_stale()):
            log.warning("marked %d interrupted tool call(s) as failed", stale)
        privacy.purge_old_chats()  # also hourly, but the scheduler may be off
        state = plans.current()
        if state.problem:
            log.warning("%s", state.problem)
        if settings.passcode_generated:
            log.warning(
                "Development passcode: %s  (from %s/.dev-secrets.json; set CUSTODIAN_PASSCODE to choose your own)",
                settings.passcode,
                settings.data_dir,
            )
        if settings.scheduler_enabled:
            scheduler.start()
        log.info(
            "Custodian %s ready (%s, plan: %s via %s, model backend: %s, search: %s, database: %s)",
            __version__,
            settings.env,
            state.plan.name,
            state.source,
            settings.model_backend,
            f"keyword + meaning ({settings.embedding_model})" if settings.embedding_model and state.plan.semantic_search else "keyword",
            settings.database_path,
        )
        try:
            yield
        finally:
            scheduler.shutdown()
            close_backend()

    production = settings.is_production
    app = FastAPI(
        title="Custodian",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )

    app.add_middleware(BodySizeLimit)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        if _is_cross_site_write(request, settings):
            log.warning("blocked a cross-site %s %s from %s", request.method, request.url.path, request.headers.get("Origin"))
            response = error(status.HTTP_403_FORBIDDEN, "Cross-site request blocked")
        else:
            response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        # API responses hold private data (chats, memories): never cache them. The web
        # app's files set their own Cache-Control in _mount_web().
        response.headers.setdefault("Cache-Control", "no-store")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", HTML_CSP)
        if request.url.path != "/health":
            log.info(
                "%s %s -> %d (%.0f ms) [%s]",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )
        return response

    def error(code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": detail})

    @app.exception_handler(ModelError)
    async def model_error(_: Request, exc: ModelError) -> JSONResponse:
        log.error("model backend error: %s", exc)
        return error(status.HTTP_502_BAD_GATEWAY, f"The model didn't respond: {exc}")

    @app.exception_handler(ConversationNotFound)
    async def conversation_missing(_: Request, exc: ConversationNotFound) -> JSONResponse:
        return error(status.HTTP_404_NOT_FOUND, "Conversation not found")

    @app.exception_handler(SkillUnavailable)
    async def skill_unavailable(_: Request, exc: SkillUnavailable) -> JSONResponse:
        return error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(tasks.TaskError)
    async def task_error(_: Request, exc: tasks.TaskError) -> JSONResponse:
        return error(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(tasks.TaskNotFound)
    async def task_missing(_: Request, exc: tasks.TaskNotFound) -> JSONResponse:
        return error(status.HTTP_404_NOT_FOUND, "Task not found")

    @app.exception_handler(memory.InvalidMemory)
    async def bad_memory(_: Request, exc: memory.InvalidMemory) -> JSONResponse:
        return error(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(permissions.UnknownScope)
    async def unknown_scope(_: Request, exc: permissions.UnknownScope) -> JSONResponse:
        return error(status.HTTP_404_NOT_FOUND, "That tool doesn't have this scope")

    @app.exception_handler(plans.PlanLimit)
    async def plan_limit(_: Request, exc: plans.PlanLimit) -> JSONResponse:
        return error(status.HTTP_402_PAYMENT_REQUIRED, str(exc))

    @app.exception_handler(licensing.LicenseError)
    async def bad_license(_: Request, exc: licensing.LicenseError) -> JSONResponse:
        return error(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Something went wrong on the server.")

    @app.api_route("/health", methods=["GET", "HEAD"], tags=["health"])
    def health() -> JSONResponse:
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return error(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable")
        return JSONResponse({"status": "ok"})

    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(data_routes.router)
    app.include_router(task_routes.router)
    app.include_router(privacy_routes.router)
    app.include_router(billing_routes.router)
    app.include_router(everyday_routes.router)
    app.include_router(jarvis_routes.router)

    web = _web_dir(settings)
    if web is not None:
        _mount_web(app, web)
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {"name": "Custodian API", "version": __version__, "health": "/health"}

    return app


def _mount_web(app: FastAPI, root: Path) -> None:
    """Serve the Expo web export (single-page app) from the same origin as the API, so
    the session cookie needs no cross-site settings. API routes are registered first and win."""
    root = root.resolve()
    index = root / "index.html"
    log.info("serving the web app from %s", root)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(full_path: str) -> FileResponse:
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                immutable = full_path.startswith(("_expo/", "assets/"))
                return FileResponse(
                    candidate,
                    headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"},
                )
            if "." in Path(full_path).name:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
            page = (root / f"{full_path}.html").resolve()
            if page.is_file() and page.is_relative_to(root):
                return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
