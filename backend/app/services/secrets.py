"""Secret storage kept out of SQLite and API responses.

Local installs use the OS credential store via `keyring` (Windows Credential
Manager, macOS Keychain, Secret Service). The interface is small on purpose so
a hosted build can swap in a KMS-backed vault without touching callers.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from ..config import get_settings


class SecretStoreError(RuntimeError):
    """Raised when the backing store cannot read or write a secret."""


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class KeyringSecretStore:
    def __init__(self, service: str) -> None:
        self._service = service

    def get(self, name: str) -> str | None:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(self._service, name)
        except KeyringError as e:
            raise SecretStoreError(f"Could not read secret {name!r}: {e}") from e

    def set(self, name: str, value: str) -> None:
        import keyring
        from keyring.errors import KeyringError

        try:
            keyring.set_password(self._service, name, value)
        except KeyringError as e:
            raise SecretStoreError(f"Could not save secret {name!r}: {e}") from e

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError

        try:
            keyring.delete_password(self._service, name)
        except PasswordDeleteError:
            pass
        except KeyringError as e:
            raise SecretStoreError(f"Could not delete secret {name!r}: {e}") from e


class MemorySecretStore:
    """Process-local store for tests."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


@lru_cache(maxsize=1)
def get_secret_store() -> SecretStore:
    settings = get_settings()
    if settings.secret_store == "memory":
        return MemorySecretStore()
    return KeyringSecretStore(settings.secret_store_service)


def user_secret_name(user_id: int, key: str) -> str:
    return f"user:{user_id}:{key}"


GMAIL_REFRESH_TOKEN = "gmail_refresh_token"
IMAP_APP_PASSWORD = "imap_app_password"
GMAIL_CLIENT_SECRET = "app:gmail_client_secret"
ANTHROPIC_API_KEY = "app:anthropic_api_key"


def claude_api_key() -> str:
    """The Claude API key: the one saved in the app first, then ANTHROPIC_API_KEY from `.env`."""

    try:
        stored = get_secret_store().get(ANTHROPIC_API_KEY)
    except SecretStoreError:
        stored = None
    if stored and stored.strip():
        return stored.strip()
    return get_settings().anthropic_api_key.strip()


WORKSPACE_ID_KEY = "anthropic_workspace_id"


def claude_workspace_id() -> str:
    """Workspace for organization-wide keys: the one saved in the app, then `.env`."""

    from ..pipeline.state import get_flag

    saved = (get_flag(WORKSPACE_ID_KEY) or "").strip()
    return saved or get_settings().anthropic_workspace_id.strip()


def claude_headers(workspace_id: str | None = None) -> dict[str, str]:
    """Extra headers for every Claude request (organization-wide keys must name a workspace)."""

    workspace = claude_workspace_id() if workspace_id is None else workspace_id.strip()
    return {"anthropic-workspace-id": workspace} if workspace else {}


def get_user_secret(user_id: int, key: str) -> str | None:
    value = get_secret_store().get(user_secret_name(user_id, key))
    return value.strip() if value and value.strip() else None


def set_user_secret(user_id: int, key: str, value: str | None) -> None:
    name = user_secret_name(user_id, key)
    cleaned = (value or "").strip()
    if cleaned:
        get_secret_store().set(name, cleaned)
    else:
        get_secret_store().delete(name)
