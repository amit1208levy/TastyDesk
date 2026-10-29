"""The iPhone stand-in for ``keyring``: the two secrets, held in memory.

On the Mac, ``core/auth.py`` reads the macOS Keychain through ``keyring``. On
the phone the app reads the iOS Keychain itself and hands the values to
``tastydesk_phone.set_credentials``, which stores them here. Nothing in this
module writes them anywhere, logs them, or prints them.

Only what ``core/auth.py`` uses is provided.
"""

from __future__ import annotations

from keyring import errors

__all__ = ["errors", "get_password", "set_password", "delete_password"]

_store: dict[tuple[str, str], str] = {}


def get_password(service: str, username: str) -> str | None:
    return _store.get((service, username))


def set_password(service: str, username: str, password: str) -> None:
    _store[(service, username)] = password


def delete_password(service: str, username: str) -> None:
    _store.pop((service, username), None)
