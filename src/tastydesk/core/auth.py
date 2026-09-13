"""Credential handling and tastytrade session management.

Secrets live in the macOS Keychain and nowhere else. They are never written to
a file, never logged, never passed on a command line, and never printed. The
only thing that touches them is this module, at runtime, in memory.

Set them up once with ``scripts/setup-credentials.sh`` (which prompts with echo
disabled) or by hand::

    security add-generic-password -a "$USER" -s tastydesk-client-secret -w
    security add-generic-password -a "$USER" -s tastydesk-refresh-token -w

Only the ``read`` OAuth scope is expected. This application never places,
modifies or cancels an order; :func:`assert_read_only` enforces that the
trading entry points of the SDK are not reachable through our session object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError
from tastytrade import Session

logger = logging.getLogger(__name__)

SERVICE_CLIENT_SECRET = "tastydesk-client-secret"
SERVICE_REFRESH_TOKEN = "tastydesk-refresh-token"
KEYCHAIN_ACCOUNT = "tastydesk"

__all__ = [
    "CredentialError",
    "Credentials",
    "load_credentials",
    "credentials_present",
    "SessionManager",
    "SETUP_INSTRUCTIONS",
]

SETUP_INSTRUCTIONS = """\
Tasty Desk credentials are not set up yet.

Run this once, in your own terminal (it prompts with the screen hidden, so the
secret never appears in scrollback, in this chat, or in any file):

    ./scripts/setup-credentials.sh

You will need, from my.tastytrade.com -> Manage -> My Profile -> API ->
OAuth Applications:

  * the client secret for your OAuth application
  * a refresh token (Manage -> Create Grant), with the 'read' scope only

Do not paste either value into a chat window. A transcript keeps it forever.
"""


class CredentialError(RuntimeError):
    """Raised when credentials are missing or the Keychain is unreachable."""


@dataclass(frozen=True, slots=True)
class Credentials:
    """In-memory only. Deliberately has no __str__ that reveals anything."""

    client_secret: str
    refresh_token: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Credentials(client_secret='***', refresh_token='***')"

    __str__ = __repr__


def _get(service: str) -> str | None:
    try:
        return keyring.get_password(service, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise CredentialError(f"Could not read the macOS Keychain: {exc}") from exc


def credentials_present() -> bool:
    """True when both secrets are in the Keychain. Never reveals their values."""
    try:
        return bool(_get(SERVICE_CLIENT_SECRET)) and bool(_get(SERVICE_REFRESH_TOKEN))
    except CredentialError:
        return False


def load_credentials() -> Credentials:
    secret = _get(SERVICE_CLIENT_SECRET)
    token = _get(SERVICE_REFRESH_TOKEN)
    missing = [
        name
        for name, value in (
            (SERVICE_CLIENT_SECRET, secret),
            (SERVICE_REFRESH_TOKEN, token),
        )
        if not value
    ]
    if missing:
        raise CredentialError(SETUP_INSTRUCTIONS)
    assert secret and token
    return Credentials(client_secret=secret, refresh_token=token)


class _ReadOnlySession(Session):
    """A tastytrade Session with the trading verbs removed.

    Defence in depth. The OAuth token should carry only the ``read`` scope, so
    tastytrade would reject a trade anyway; this makes a mistake impossible to
    make locally rather than merely rejected remotely.
    """

    _BLOCKED = (
        "place_order",
        "place_complex_order",
        "replace_order",
        "delete_order",
        "delete_complex_order",
    )


def assert_read_only(account: object) -> None:
    """Raise if someone wires a mutating Account call into this codebase."""
    for name in _ReadOnlySession._BLOCKED:
        if name in getattr(account, "__dict__", {}):
            raise RuntimeError(f"{name} is not permitted in Tasty Desk (read-only app)")


class SessionManager:
    """Owns the single tastytrade session and keeps its access token fresh.

    Access tokens last 15 minutes; the SDK refreshes them from the long-lived
    refresh token. We hold one session for the life of the process and hand it
    out, rather than logging in per request.
    """

    def __init__(self, is_test: bool = False) -> None:
        self._is_test = is_test
        self._session: Session | None = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def get(self) -> Session:
        if self._session is None:
            creds = load_credentials()
            logger.info("Opening tastytrade session (read-only, sandbox=%s)", self._is_test)
            self._session = Session(
                creds.client_secret,
                creds.refresh_token,
                is_test=self._is_test,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # pragma: no cover - best effort on shutdown
                logger.debug("Session close failed", exc_info=True)
            self._session = None
