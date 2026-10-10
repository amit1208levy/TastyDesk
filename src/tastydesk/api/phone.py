"""The phone's way in: a second listener that answers only a paired phone.

The dashboard's own listener stays what it always was — 127.0.0.1, no token,
for the browser on this Mac. The phone gets a separate one, also bound to
127.0.0.1, that a device-authenticated tunnel (``tailscale serve``) forwards
to. Nothing here listens on a network interface; Tailscale decides which
devices reach the Mac at all, and this module decides which requests from
them are answered.

On the phone listener every request must carry the pairing token, compared in
constant time against a hash kept in the Keychain. The token itself is never
stored on this machine — it is shown once, at pairing, and lives on the phone.
Only ``/api`` is served there; the app ships its own copy of the interface.

The dashboard listener, for its part, now answers only requests addressed to a
loopback name. Pointing a tunnel at the wrong port, or a page elsewhere that
rebinds its DNS to 127.0.0.1, then gets a refusal rather than the account.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import keyring
from keyring.errors import KeyringError
from starlette.middleware.cors import CORSMiddleware

from tastydesk.core.auth import KEYCHAIN_ACCOUNT

SERVICE_PHONE_TOKEN = "tastydesk-phone-token-sha256"
DEFAULT_PHONE_PORT = 8788
# The origin a Capacitor app's bundled pages run under on iOS.
PHONE_ORIGIN = "capacitor://localhost"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def phone_port() -> int:
    return int(os.environ.get("TASTYDESK_PHONE_PORT", DEFAULT_PHONE_PORT))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_pairing() -> str:
    """Make a token, keep only its hash, and hand the token back exactly once."""
    token = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE_PHONE_TOKEN, KEYCHAIN_ACCOUNT, _digest(token))
    return token


def forget_pairing() -> bool:
    try:
        keyring.delete_password(SERVICE_PHONE_TOKEN, KEYCHAIN_ACCOUNT)
    except KeyringError:
        return False
    return True


def paired_digest() -> str | None:
    """The stored hash, or None when no phone is paired or the Keychain is shut."""
    try:
        return keyring.get_password(SERVICE_PHONE_TOKEN, KEYCHAIN_ACCOUNT) or None
    except KeyringError:
        return None


async def _refuse(
    scope: Scope, send: Send, status: int, detail: str, headers: list | None = None
) -> None:
    # Deliberately bare: a stranger learns that the answer is no, not why.
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), *(headers or [])],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return ""


def _host_name(host_header: str) -> str:
    host_header = host_header.lower()
    if host_header.startswith("["):
        return host_header.split("]", 1)[0] + "]"
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


class _RequireToken:
    def __init__(self, app: ASGIApp, digest: str) -> None:
        self.app = app
        self.digest = digest

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not scope.get("path", "").startswith("/api/"):
            await _refuse(scope, send, 404, "Not found")
            return
        auth = _header(scope, b"authorization")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token or not hmac.compare_digest(
            _digest(token.strip()), self.digest
        ):
            await _refuse(scope, send, 401, "Unauthorized", [(b"www-authenticate", b"Bearer")])
            return
        await self.app(scope, receive, send)


class PhoneGate:
    """Route each connection by the listener it arrived on.

    ``digest`` is read once, at start-up: unpairing takes effect on the next
    restart, and a request never waits on the Keychain.
    """

    def __init__(self, app: ASGIApp, port: int | None, digest: str | None) -> None:
        self.app = app
        self.port = port
        self.phone_app: ASGIApp | None = None
        if port is not None and digest:
            self.phone_app = CORSMiddleware(
                _RequireToken(app, digest),
                allow_origins=[PHONE_ORIGIN],
                allow_methods=["GET", "POST", "DELETE", "PUT"],
                allow_headers=["Authorization", "Content-Type", "Accept", "X-Era"],
                max_age=600,
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        server = scope.get("server") or (None, None)
        if self.port is not None and server[1] == self.port:
            # The phone has no use for a socket; only plain requests go through.
            if self.phone_app is None or scope["type"] != "http":
                await _refuse(scope, send, 401, "Unauthorized")
                return
            await self.phone_app(scope, receive, send)
            return

        if _host_name(_header(scope, b"host")) not in LOOPBACK_HOSTS:
            await _refuse(scope, send, 403, "Forbidden")
            return
        await self.app(scope, receive, send)
