"""API key storage in the OS credential store (Windows Credential Manager via
``keyring``). Keys are never written to the database, logs or API responses.

``ARBITER_SECRET_BACKEND=memory`` swaps in an in-process store; the test suite
uses it so tests never touch the real credential store.
"""

from __future__ import annotations

import threading

from . import config

SERVICE = "arbiter"


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._data[name] = value

    def delete(self, name: str) -> None:
        with self._lock:
            self._data.pop(name, None)


class KeyringStore:
    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass


_memory = MemoryStore()


def store() -> MemoryStore | KeyringStore:
    return _memory if config.settings.secret_backend == "memory" else KeyringStore()


def _name(endpoint_id: int) -> str:
    return f"endpoint-{endpoint_id}"


def get_key(endpoint_id: int) -> str | None:
    return store().get(_name(endpoint_id))


def set_key(endpoint_id: int, value: str) -> None:
    store().set(_name(endpoint_id), value)


def delete_key(endpoint_id: int) -> None:
    store().delete(_name(endpoint_id))


def mask(value: str | None) -> str | None:
    """Masked hint like '••••7f3a'. Shows at most the last 4 characters."""
    if not value:
        return None
    tail = value[-4:] if len(value) >= 12 else ""
    return "••••" + tail


def key_status(endpoint_id: int) -> dict:
    try:
        value = get_key(endpoint_id)
    except Exception:  # credential store unavailable
        return {"has_key": False, "masked": None, "store_error": True}
    return {"has_key": bool(value), "masked": mask(value), "store_error": False}
