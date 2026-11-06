import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    # Derives a valid 32-byte Fernet key from SECRET_KEY so no extra env var
    # is needed for this scaffold. In production, use a dedicated key from a
    # secrets manager (KMS/Vault) instead of deriving from SECRET_KEY.
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def mask(raw: str, visible: int = 4) -> str:
    if len(raw) <= visible:
        return "•" * len(raw)
    return "•" * (len(raw) - visible) + raw[-visible:]
