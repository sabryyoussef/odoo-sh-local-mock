from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().session_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def protect_token(raw: str) -> str:
    return _fernet().encrypt(raw.encode("utf-8")).decode("utf-8")


def reveal_token(protected: str | None) -> str | None:
    if not protected:
        return None
    try:
        return _fernet().decrypt(protected.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
