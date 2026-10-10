"""Password hashing and admin session token helpers.

No custom cryptography here: passwords are hashed with argon2-cffi
(``argon2.PasswordHasher``) and session tokens are random values produced by
:mod:`secrets`, stored only as a SHA-256 hash.
"""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# A single shared hasher; argon2 is stateless and thread-safe for verify/hash.
_password_hasher = PasswordHasher()

# Number of random bytes for a session token (token_urlsafe uses ~1.33 chars/byte).
SESSION_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage in ``admins.password_hash``."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True if ``password`` matches ``password_hash``, False otherwise.

    Never raises on a wrong password or a malformed/unknown hash.
    """
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_session_token() -> str:
    """Generate a new raw session token (only ever shown to the client)."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest stored in ``admin_sessions.token_hash``."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def session_token_matches(raw_token: str, token_hash: str) -> bool:
    """Constant-time check that ``raw_token`` hashes to ``token_hash``."""
    return hmac.compare_digest(hash_session_token(raw_token), token_hash)
