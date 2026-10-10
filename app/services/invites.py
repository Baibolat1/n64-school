"""Invite code generation, storage and redemption.

Codes are shown to the admin exactly once. Only an HMAC-SHA256 hash (keyed with
``settings.SECRET_KEY``) is stored, so a database leak does not expose the
codes and short codes cannot be brute-forced offline without the key.

Transaction policy (mirrors ``app.services.admin_auth``): this service layer
owns commits for write operations.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.admin import Admin
from app.models.invite import Invite
from app.models.student import Student
from app.services import aliases as aliases_service

# 32-symbol alphabet without easily confused characters: no O/0, no I/1.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 12 symbols = 3 groups of 4, i.e. 12 * 5 = 60 bits of entropy.
_CODE_LENGTH = 12
_GROUP_SIZE = 4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_invite_code() -> str:
    """Generate a new raw invite code, e.g. ``K7QP-3MZR-9XTV``.

    Uses :func:`secrets.token_bytes` (a CSPRNG) and maps each byte uniformly
    onto the alphabet (256 is a multiple of 32, so the mapping is unbiased).
    """
    chars = [_ALPHABET[b % len(_ALPHABET)] for b in secrets.token_bytes(_CODE_LENGTH)]
    groups = (
        "".join(chars[i : i + _GROUP_SIZE])
        for i in range(0, _CODE_LENGTH, _GROUP_SIZE)
    )
    return "-".join(groups)


def normalize_invite_code(raw_code: str) -> str:
    """Canonicalize a user-entered code: drop whitespace/dashes, upper-case.

    Applied both when storing (hashing) and when checking a submitted code, so
    the same code always maps to the same hash regardless of formatting.
    """
    return "".join(raw_code.split()).replace("-", "").upper()


def hash_invite_code(raw_code: str) -> str:
    """Return the HMAC-SHA256 hex digest stored in ``invites.code_hash``."""
    normalized = normalize_invite_code(raw_code)
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_invite(db: Session, admin: Admin, ttl_days: int) -> str:
    """Create an invite and return its raw code (shown to the admin once)."""
    raw_code = generate_invite_code()
    invite = Invite(
        code_hash=hash_invite_code(raw_code),
        created_by=admin.id,
        expires_at=_utcnow() + timedelta(days=ttl_days),
        max_uses=1,
        used_count=0,
    )
    db.add(invite)
    db.commit()
    return raw_code


def redeem_invite(db: Session, raw_code: str) -> Student | None:
    """Redeem an invite code, creating a student on success.

    Returns the new :class:`Student`, or ``None`` for every failure case
    (unknown, expired, or already used). Callers must not distinguish the
    reasons -- a uniform failure keeps codes from being enumerated.

    Race safety: a single conditional ``UPDATE ... WHERE used_count < max_uses
    AND expires_at > now`` atomically claims the invite. Only the request whose
    UPDATE reports one affected row proceeds to create the student, so two
    concurrent requests with the same code cannot both succeed.
    """
    code_hash = hash_invite_code(raw_code)
    now = _utcnow()

    result = db.execute(
        update(Invite)
        .where(
            Invite.code_hash == code_hash,
            Invite.used_count < Invite.max_uses,
            Invite.expires_at > now,
        )
        .values(used_count=Invite.used_count + 1, used_at=now)
    )
    if result.rowcount != 1:
        db.rollback()
        return None

    invite_id = db.execute(
        select(Invite.id).where(Invite.code_hash == code_hash)
    ).scalar_one()

    student = Student(
        alias=aliases_service.generate_alias(db),
        invite_id=invite_id,
    )
    db.add(student)
    db.commit()
    return student


def list_invites(db: Session) -> list[tuple[Invite, str]]:
    """Return invites with the creating admin's username, newest first."""
    stmt = (
        select(Invite, Admin.username)
        .join(Admin, Admin.id == Invite.created_by)
        .order_by(Invite.created_at.desc(), Invite.id.desc())
    )
    return list(db.execute(stmt).all())
