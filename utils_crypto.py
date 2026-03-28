import base64
import os
from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from flask import current_app


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    secret_key = current_app.secret_key
    if not secret_key:
        raise RuntimeError("Flask secret_key must be heavily secured to use encryption.")

    if isinstance(secret_key, str):
        secret_key_bytes = secret_key.encode("utf-8")
    else:
        secret_key_bytes = secret_key

    # Derive a strong 32-byte key from the Flask SECRET_KEY using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ecomfans_api_key_salt_v1",  # Static salt is acceptable here as the application secret is already private
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key_bytes))
    return Fernet(key)


def encrypt_api_key(raw_key: str) -> str:
    """Encrypt a plaintext API key."""
    if not raw_key:
        return raw_key

    # Already encrypted (safety check)
    if raw_key.startswith("gAAAAA"):
        return raw_key

    fernet = _get_fernet()
    return fernet.encrypt(raw_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt an API key stored in the database."""
    if not encrypted_key:
        return encrypted_key

    # If it's not a Fernet token, assume it's legacy plaintext.
    if not encrypted_key.startswith("gAAAAA"):
        return encrypted_key

    fernet = _get_fernet()
    try:
        return fernet.decrypt(encrypted_key.encode("utf-8")).decode("utf-8")
    except Exception:
        # If decryption fails, return as-is (might be an invalid key or a coincidental string).
        return encrypted_key
