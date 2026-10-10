"""Data access for admins and their sessions.

This layer performs queries only: it never commits. The calling layer
(``app.services.admin_auth``, or the seed script) is responsible for
``db.commit()``. ``flush()`` is used where an id is needed within the same
transaction.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin import Admin, AdminSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_admin_by_username(db: Session, username: str) -> Admin | None:
    stmt = select(Admin).where(Admin.username == username)
    return db.execute(stmt).scalar_one_or_none()


def get_admin_by_id(db: Session, admin_id: int) -> Admin | None:
    return db.get(Admin, admin_id)


def create_admin(
    db: Session,
    *,
    username: str,
    password_hash: str,
    is_active: bool = True,
) -> Admin:
    admin = Admin(username=username, password_hash=password_hash, is_active=is_active)
    db.add(admin)
    db.flush()
    return admin


def create_session(
    db: Session,
    *,
    admin_id: int,
    token_hash: str,
    expires_at: datetime,
) -> AdminSession:
    session = AdminSession(
        admin_id=admin_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()
    return session


def get_active_session_by_token_hash(
    db: Session, token_hash: str
) -> AdminSession | None:
    """Return a session that is neither revoked nor expired, or None."""
    stmt = select(AdminSession).where(
        AdminSession.token_hash == token_hash,
        AdminSession.revoked_at.is_(None),
        AdminSession.expires_at > _utcnow(),
    )
    return db.execute(stmt).scalar_one_or_none()


def revoke_session(db: Session, session: AdminSession) -> None:
    session.revoked_at = _utcnow()
    db.flush()


def revoke_all_sessions_for_admin(db: Session, admin_id: int) -> int:
    """Revoke every still-active session of an admin. Returns rows affected."""
    sessions = db.execute(
        select(AdminSession).where(
            AdminSession.admin_id == admin_id,
            AdminSession.revoked_at.is_(None),
        )
    ).scalars()
    count = 0
    for session in sessions:
        session.revoked_at = _utcnow()
        count += 1
    db.flush()
    return count
