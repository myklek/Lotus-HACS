"""Firebase Realtime Database stream client for lifecounter.app live games.

Kept free of Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import re
import time
from typing import Any

import aiohttp

from .const import API_KEY, AUTH_URL, DB_URL, TOKEN_URL

_LOGGER = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^[A-HJKMNP-Z2-9]{5}$")
_URL_RE = re.compile(r"/watch/([A-Za-z0-9]+)")


def normalize_code(value: str | None) -> str | None:
    """Return a valid 5 char game code from a code or watch URL, else None."""
    raw = str(value or "").strip()
    match = _URL_RE.search(raw)
    if match:
        raw = match.group(1)
    code = re.sub(r"[^A-Z0-9]", "", raw.upper())
    return code if _CODE_RE.match(code) else None


def apply_event(root: Any, path: str | None, data: Any, merge: bool) -> Any:
    """Apply a Firebase put/patch event to a local copy of the tree."""
    parts = [p for p in str(path or "/").split("/") if p]
    if merge:
        for key, value in (data or {}).items():
            sub = parts + [p for p in key.split("/") if p]
            root = apply_event(root, "/" + "/".join(sub), value, False)
        return root
    if not parts:
        return data
    if isinstance(root, list):
        root = {str(i): v for i, v in enumerate(root) if v is not None}
    if not isinstance(root, dict):
        root = {}
    node = root
    for part in parts[:-1]:
        child = node.get(part)
        if isinstance(child, list):
            child = {str(i): v for i, v in enumerate(child) if v is not None}
            node[part] = child
        elif not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    if data is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = data
    return root


class LotusAuth:
    """Anonymous Firebase auth, used only if the database refuses anonymous reads."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0

    async def token(self, force: bool = False) -> str:
        if not force and self._id_token and self._expires_at - time.time() > 300:
            return self._id_token
        if self._refresh_token:
            try:
                async with self._session.post(
                    f"{TOKEN_URL}/v1/token?key={API_KEY}",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp.raise_for_status()
                    self._store(await resp.json(content_type=None))
                    return self._id_token  # type: ignore[return-value]
            except aiohttp.ClientError:
                self._refresh_token = None
        async with self._session.post(
            f"{AUTH_URL}/v1/accounts:signUp?key={API_KEY}",
            json={"returnSecureToken": True},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            self._store(await resp.json(content_type=None))
        return self._id_token  # type: ignore[return-value]

    def _store(self, data: dict[str, Any]) -> None:
        self._id_token = data.get("idToken") or data.get("id_token")
        self._refresh_token = data.get("refreshToken") or data.get("refresh_token")
        self._expires_at = time.time() + float(
            data.get("expiresIn") or data.get("expires_in") or 3600
        )


class StreamDenied(Exception):
    """The database cancelled the stream (permission denied)."""


class StreamAuthRevoked(Exception):
    """The auth token expired mid-stream."""


class LotusGameStream:
    """Follows liveGames/<code> over Server-Sent Events and reconnects forever."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        code: str,
        on_data: Callable[[dict[str, Any] | None], None],
        on_connection: Callable[[str], None],
        db_url: str | None = None,
    ) -> None:
        self._session = session
        self.code = code
        self._on_data = on_data
        self._on_connection = on_connection
        self._db_url = (db_url or DB_URL).rstrip("/")
        self._auth = LotusAuth(session)
        self._use_auth = False
        self._tree: Any = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name=f"lotus_mtg stream {self.code}"
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._stream_once()
                backoff = 1.0
                self._on_connection("error")
            except asyncio.CancelledError:
                raise
            except StreamAuthRevoked:
                continue
            except StreamDenied:
                if not self._use_auth:
                    _LOGGER.debug("Anonymous read denied, retrying with auth")
                    self._use_auth = True
                    continue
                self._on_connection("denied")
                backoff = 60.0
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.debug("Lotus stream %s error: %s", self.code, err)
                self._on_connection("error")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _stream_once(self) -> None:
        params: dict[str, str] = {}
        if self._use_auth:
            params["auth"] = await self._auth.token()
        url = f"{self._db_url}/liveGames/{self.code}.json"
        async with self._session.get(
            url,
            params=params,
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
            timeout=aiohttp.ClientTimeout(total=None, connect=20, sock_read=120),
        ) as resp:
            if resp.status in (401, 403):
                raise StreamDenied
            resp.raise_for_status()
            self._on_connection("open")
            event: str | None = None
            data_lines: list[str] = []
            async for raw in resp.content:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if event:
                        self._dispatch(event, "\n".join(data_lines))
                    event, data_lines = None, []
                elif line.startswith(":"):
                    continue
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))

    def _dispatch(self, event: str, payload: str) -> None:
        if event in ("put", "patch"):
            try:
                msg = json.loads(payload)
            except ValueError:
                return
            if not isinstance(msg, dict):
                return
            self._tree = apply_event(
                self._tree, msg.get("path"), msg.get("data"), event == "patch"
            )
            self._on_data(self._tree if isinstance(self._tree, dict) else None)
        elif event == "cancel":
            raise StreamDenied
        elif event == "auth_revoked":
            raise StreamAuthRevoked
