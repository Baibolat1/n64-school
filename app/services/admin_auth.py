"""Admin authentication business logic.

Transaction policy: this service layer owns commits for write operations
(``login`` and ``logout``). The repository layer only queries/flushes.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import logger
from app.core.security import (
    generate_session_token,
    hash_session_token,
    session_token_matches,
    verify_password,
)
from app.models.admin import Admin
from app.repositories import admins as admins_repo


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime read from SQLite (stored naive-UTC) to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def login(db: Session, username: str, password: str) -> str | None:
    """Authenticate an admin and start a session.

    Returns the raw session token on success (the caller puts it in a cookie),
    or None if the credentials are invalid or the admin is inactive. The raw
    token is never stored -- only its hash is persisted.
    """
    # Refuse oversized input before the lookup and bcrypt; a uniform ``None``
    # keeps the limit from becoming an oracle for which field was too long.
    if len(username) > settings.MAX_ADMIN_USERNAME_LENGTH:
        logger.info("admin_login_failed")
        return None
    if len(password) > settings.MAX_PASSWORD_LENGTH:
        logger.info("admin_login_failed")
        return None
    admin = admins_repo.get_admin_by_username(db, username)
    if admin is None or not admin.is_active:
        logger.info("admin_login_failed")
        return None
    if not verify_password(admin.password_hash, password):
        logger.info("admin_login_failed")
        return None

    raw_token = generate_session_token()
    expires_at = _utcnow() + timedelta(hours=settings.ADMIN_SESSION_TTL_HOURS)
    admins_repo.create_session(
        db,
        admin_id=admin.id,
        token_hash=hash_session_token(raw_token),
        expires_at=expires_at,
    )
    db.commit()
    return raw_token


def resolve_session(db: Session, raw_token: str) -> Admin | None:
    """Return the admin owning a valid session token, or None."""
    if not raw_token:
        return None

    token_hash = hash_session_token(raw_token)
    session = admins_repo.get_active_session_by_token_hash(db, token_hash)
    if session is None:
        return None
    # Defense in depth: re-check revoked/expiry and the constant-time match.
    if session.revoked_at is not None:
        return None
    if _as_utc(session.expires_at) <= _utcnow():
        return None
    if not session_token_matches(raw_token, session.token_hash):
        return None

    admin = admins_repo.get_admin_by_id(db, session.admin_id)
    if admin is None or not admin.is_active:
        return None
    return admin


def logout(db: Session, raw_token: str) -> None:
    """Revoke the session identified by ``raw_token`` (no-op if not active)."""
    if not raw_token:
        return

    token_hash = hash_session_token(raw_token)
    session = admins_repo.get_active_session_by_token_hash(db, token_hash)
    if session is None:
        return

    admins_repo.revoke_session(db, session)
    db.commit()
