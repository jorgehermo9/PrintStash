"""Authenticated encryption for credentials persisted by PrintStash."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "enc:v1:"


def _key_material() -> bytes:
    configured = str(settings.secrets_key).strip()
    if configured:
        return configured.encode()

    path = Path(settings.secrets_key_file)
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = base64.urlsafe_b64encode(os.urandom(32))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes().strip()
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated + b"\n")
        return generated


# The cipher for the key last read, and what identified that key: the
# configured value, or the key file's path and stat. Encrypting a credential
# is per request, while the key changes only on a restore or an edit.
_cached: tuple[tuple[object, ...], Fernet] | None = None


def _identity() -> tuple[object, ...] | None:
    configured = str(settings.secrets_key).strip()
    if configured:
        return ("configured", configured)
    path = Path(settings.secrets_key_file)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return ("file", str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _fernet() -> Fernet:
    global _cached
    identity = _identity()
    if identity is not None and _cached is not None and _cached[0] == identity:
        return _cached[1]
    derived = hashlib.sha256(_key_material()).digest()
    cipher = Fernet(base64.urlsafe_b64encode(derived))
    # A key generated just now has a stat only once it was written.
    identity = identity or _identity()
    _cached = None if identity is None else (identity, cipher)
    return cipher


def encrypt_secret(value: str | None) -> str | None:
    if value is None or value.startswith(_PREFIX):
        return value
    token = _fernet().encrypt(value.encode()).decode()
    return f"{_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None or not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "stored credential cannot be decrypted; restore VAULT_SECRETS_KEY "
            "or the secrets key file"
        ) from exc
