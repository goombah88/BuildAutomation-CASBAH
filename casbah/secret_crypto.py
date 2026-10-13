"""Local symmetric encryption for secrets stored in ~/.api_variables_env, so values
like Vault AppRole role_id/secret_id aren't kept as plain text there.

The encryption key lives in a SEPARATE file (~/.casbah/secret.key, chmod 600) from the
ciphertext in ~/.api_variables_env, so an accidental leak of either file alone (pasted
somewhere, synced to a backup, etc.) doesn't also expose the other. This is not a
substitute for real secrets management in production (Vault Agent with a response-
wrapped, single-use secret_id is the right answer there) — it's a real improvement for
local dev storage, not just obfuscation, as long as the two files stay separate.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_PATH = Path.home() / ".casbah" / "secret.key"


def _load_or_create_key() -> bytes:
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()

    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    KEY_PATH.chmod(0o600)
    return key


def encrypt(value: str) -> str:
    return Fernet(_load_or_create_key()).encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    """Returns None (rather than raising) on any decryption failure, so callers can
    treat it the same as "not configured"."""
    try:
        return Fernet(_load_or_create_key()).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
