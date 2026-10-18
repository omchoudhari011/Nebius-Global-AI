"""The Custodian server's API, as the desktop app uses it."""

from __future__ import annotations

import platform

import httpx

from . import __version__

DEVICE = f"Custodian desktop on {platform.node() or 'Windows'}"


class NotSignedIn(RuntimeError):
    pass


class Client:
    def __init__(self, server: str, token: str | None = None) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self._http = httpx.Client(base_url=self.server, timeout=httpx.Timeout(30, connect=5),
                                  headers={"User-Agent": f"Custodian desktop/{__version__} (Windows)",
                                           "X-Custodian-Device": DEVICE})

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise NotSignedIn("The session expired or was signed out; sign in again.")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = None
            raise RuntimeError(str(detail or f"The server answered {resp.status_code}"))
        return resp

    def login(self, passcode: str) -> str:
        resp = self._http.post("/auth/login", json={"passcode": passcode, "client": "native"})
        self._check(resp)
        self.token = resp.json()["token"]
        return self.token

    def me(self) -> dict:
        return self._check(self._http.get("/auth/me", headers=self._headers())).json()

    def features(self) -> dict:
        return self._check(self._http.get("/features", headers=self._headers())).json()

    def chat(self, message: str, conversation_id: int | None = None) -> dict:
        resp = self._http.post("/chat", headers=self._headers(), timeout=300,
                               json={"message": message, "conversation_id": conversation_id})
        return self._check(resp).json()

    def warm(self) -> None:
        """Ask the server to load its model now, while you're still talking."""
        try:
            self._http.post("/warm", headers=self._headers(), timeout=5)
        except httpx.HTTPError:
            pass

    def transcribe(self, wav: bytes) -> str:
        resp = self._http.post("/voice/transcribe", headers={**self._headers(), "Content-Type": "audio/wav"},
                               content=wav, timeout=120)
        return self._check(resp).json()["text"]

    def next_action(self) -> dict | None:
        """Long-poll for the next PC action (the server holds the request up to ~25 s)."""
        resp = self._check(self._http.get("/desktop/next", headers=self._headers(), timeout=40))
        return None if resp.status_code == 204 else resp.json()

    def send_result(self, action_id: int, ok: bool, result: object = None, error: str | None = None) -> None:
        self._http.post(f"/desktop/results/{action_id}", headers=self._headers(), timeout=60,
                        json={"ok": ok, "result": result, "error": error})

    def briefing(self, force: bool = False) -> dict:
        """Today's morning briefing to read out ({"text"}), or {"skipped": why} (once a day unless forced)."""
        return self._check(self._http.post("/briefing", params={"force": "true"} if force else None,
                                           headers=self._headers(), timeout=300)).json()

    def index_config(self) -> dict:
        """Whether to index the PC's files for search, which folders, and what the server has."""
        return self._check(self._http.get("/desktop/index/config", headers=self._headers(), timeout=60)).json()

    def index_files(self, files: list[dict], removed: list[str]) -> dict:
        resp = self._http.post("/desktop/index", headers=self._headers(), timeout=300,
                               json={"files": files, "removed": removed})
        return self._check(resp).json()

    def close(self) -> None:
        self._http.close()
