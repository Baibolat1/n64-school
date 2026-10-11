"""Student self-registration: open email+password, then QR confirmation.

Two ways in, on top of the admin invite codes (which are untouched):

* while fewer than ``OPEN_REGISTRATION_LIMIT`` students exist, anyone may
  register with an email and a password;
* afterwards the only self-service way in is a QR code shown by a novice and
  scanned/confirmed by an existing student (the WeChat-style flow).

The QR token is a random value; the database stores only a keyed hash (as
invites do), while the plaintext is what the QR image encodes. The novice's own
browser carries the plaintext token in a short-lived signed cookie so the page
can re-render, refresh and poll without any server-side session.

Transaction policy (mirrors the other services): this layer owns commits for
write operations.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, insert, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password
from app.models.registration_qr import (
    QR_STATUS_CONFIRMED,
    QR_STATUS_EXPIRED,
    QR_STATUS_PENDING,
    QR_STATUS_USED,
    RegistrationQrToken,
)
from app.models.student import Student
from app.services import aliases as aliases_service

# Deliberately simple: one @, a dot in the domain, no spaces. Addresses are only
# ever used as a login handle, never for mail, so this rejects the obviously
# malformed without pretending to validate deliverability.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 32 random bytes -> ~43 URL-safe characters, far beyond guessing.
_TOKEN_BYTES = 32

# Salt for the signed cookie that carries the novice's plaintext QR token, so it
# cannot be confused with a student session or any other signed value.
_QR_COOKIE_SALT = "registration-qr"


def _qr_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_QR_COOKIE_SALT)


def dump_qr_cookie(raw_token: str) -> str:
    """Sign a plaintext QR token for the novice's own browser cookie."""
    return _qr_serializer().dumps({"token": raw_token})


def load_qr_cookie(cookie_value: str | None) -> str | None:
    """Return the plaintext QR token from a signed cookie, or ``None``.

    The cookie is valid a little past the token's own TTL so an expired-token
    page can still read it and offer "Обновить код".
    """
    if not cookie_value:
        return None
    try:
        data = _qr_serializer().loads(
            cookie_value, max_age=(settings.QR_TOKEN_TTL_HOURS + 24) * 3600
        )
    except (BadSignature, SignatureExpired):
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) else None


class RegistrationError(str, Enum):
    """Machine-readable reasons self-registration can be refused."""

    CLOSED = "closed"
    EMAIL_TAKEN = "email_taken"
    INVALID_EMAIL = "invalid_email"
    PASSWORD_TOO_SHORT = "password_too_short"
    INVALID_TOKEN = "invalid_token"
    TOKEN_EXPIRED = "token_expired"
    NOT_CONFIRMED = "not_confirmed"


@dataclass(frozen=True)
class RegistrationResult:
    """Outcome of an email+password (or QR password) registration attempt."""

    ok: bool
    student: Student | None = None
    reason: RegistrationError | None = None


@dataclass(frozen=True)
class QrStartResult:
    """Outcome of creating or refreshing a registration QR token."""

    ok: bool
    raw_token: str | None = None
    reason: RegistrationError | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime read from SQLite (stored naive-UTC) to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalize_email(raw: str) -> str:
    """Canonicalize an email: trim and lower-case, so uniqueness is stable."""
    return raw.strip().lower()


def _hash_token(raw_token: str) -> str:
    """HMAC-SHA256 hex digest of a QR token, keyed with ``SECRET_KEY``."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def student_count(db: Session) -> int:
    """Total number of students ever created (all registration paths)."""
    return db.execute(select(func.count()).select_from(Student)).scalar_one()


def registration_open(db: Session) -> bool:
    """Whether open email+password registration is still available."""
    return student_count(db) < settings.OPEN_REGISTRATION_LIMIT


def _email_taken(db: Session, email: str) -> bool:
    stmt = select(Student.id).where(Student.email == email).limit(1)
    return db.execute(stmt).first() is not None


def register_with_password(
    db: Session, raw_email: str, password: str
) -> RegistrationResult:
    """Create a student from an email and password (open-registration path).

    The cap is enforced by an ``INSERT ... SELECT ... WHERE`` whose condition
    re-counts the students in the same statement as the insert, so two requests
    racing at the boundary cannot both slip a student past the limit. A unique
    index on ``students.email`` backs the availability pre-check against a
    concurrent double-submit.
    """
    email = normalize_email(raw_email)
    if not _EMAIL_RE.match(email):
        return RegistrationResult(ok=False, reason=RegistrationError.INVALID_EMAIL)
    if len(password) < settings.MIN_PASSWORD_LENGTH:
        return RegistrationResult(
            ok=False, reason=RegistrationError.PASSWORD_TOO_SHORT
        )
    if _email_taken(db, email):
        return RegistrationResult(ok=False, reason=RegistrationError.EMAIL_TAKEN)

    student = _insert_open_student(db, email=email, password=password)
    if student is None:
        # Either the cap was reached between the checks, or the email was taken
        # by a concurrent request; reopen the form to let the caller retry.
        reason = (
            RegistrationError.CLOSED
            if not registration_open(db)
            else RegistrationError.EMAIL_TAKEN
        )
        return RegistrationResult(ok=False, reason=reason)
    return RegistrationResult(ok=True, student=student)


def _insert_open_student(
    db: Session, *, email: str, password: str
) -> Student | None:
    """Guarded insert used only while open registration is available.

    The insert is a no-op (0 rows) when ``COUNT(students)`` has already reached
    the limit, which makes the cap atomic under SQLite's write serialization.
    """
    alias = aliases_service.generate_alias(db)
    password_hash = hash_password(password)
    current_count = (
        select(func.count()).select_from(Student).scalar_subquery()
    )
    stmt = insert(Student).from_select(
        ["alias", "email", "password_hash", "is_banned", "alias_strategy"],
        select(
            literal(alias),
            literal(email),
            literal(password_hash),
            literal(False),
            literal("constant"),
        ).where(current_count < settings.OPEN_REGISTRATION_LIMIT),
    )
    try:
        result = db.execute(stmt)
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    if result.rowcount != 1:
        db.rollback()
        return None
    return db.execute(
        select(Student).where(Student.email == email)
    ).scalar_one_or_none()


def _create_student(
    db: Session, *, email: str, password: str
) -> Student | None:
    """Insert a student with credentials; ``None`` if the email was just taken.

    Unconditional (no open-registration cap): used by the QR path, which is the
    way in *after* the limit has been reached.
    """
    student = Student(
        alias=aliases_service.generate_alias(db),
        email=email,
        password_hash=hash_password(password),
    )
    db.add(student)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return student


# --- QR registration ----------------------------------------------------------


def start_qr_registration(db: Session, raw_email: str) -> QrStartResult:
    """Create a fresh pending QR token for ``raw_email``.

    Any earlier pending token for the same address is expired first, so a
    refreshed page cannot leave a trail of live codes.
    """
    email = normalize_email(raw_email)
    if not _EMAIL_RE.match(email):
        return QrStartResult(ok=False, reason=RegistrationError.INVALID_EMAIL)
    if _email_taken(db, email):
        return QrStartResult(ok=False, reason=RegistrationError.EMAIL_TAKEN)

    return QrStartResult(ok=True, raw_token=_new_qr_token(db, email))


def refresh_qr_registration(db: Session, raw_token: str) -> QrStartResult:
    """Replace a pending/expired token with a new one for the same email."""
    token = get_qr_token(db, raw_token)
    if token is None:
        return QrStartResult(ok=False, reason=RegistrationError.INVALID_TOKEN)
    if token.status == QR_STATUS_USED:
        return QrStartResult(ok=False, reason=RegistrationError.INVALID_TOKEN)
    if _email_taken(db, token.email):
        return QrStartResult(ok=False, reason=RegistrationError.EMAIL_TAKEN)
    # Retire the old code so only the newest one is live.
    _expire_token(db, token)
    return QrStartResult(ok=True, raw_token=_new_qr_token(db, token.email))


def _new_qr_token(db: Session, email: str) -> str:
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    token = RegistrationQrToken(
        email=email,
        token_hash=_hash_token(raw_token),
        expires_at=_utcnow() + timedelta(hours=settings.QR_TOKEN_TTL_HOURS),
        status=QR_STATUS_PENDING,
    )
    db.add(token)
    db.commit()
    return raw_token


def _expire_token(db: Session, token: RegistrationQrToken) -> None:
    db.execute(
        update(RegistrationQrToken)
        .where(RegistrationQrToken.id == token.id)
        .values(status=QR_STATUS_EXPIRED)
    )
    db.commit()


def get_qr_token(db: Session, raw_token: str) -> RegistrationQrToken | None:
    """Load a token row by its plaintext value, or ``None``."""
    if not raw_token:
        return None
    stmt = select(RegistrationQrToken).where(
        RegistrationQrToken.token_hash == _hash_token(raw_token)
    )
    return db.execute(stmt).scalar_one_or_none()


def effective_status(token: RegistrationQrToken) -> str:
    """The token's status, treating a passed ``expires_at`` as expired.

    Expiry is lazy (there is no background job): a pending token whose deadline
    has passed reads as expired here.
    """
    if token.status == QR_STATUS_PENDING and _as_utc(token.expires_at) <= _utcnow():
        return QR_STATUS_EXPIRED
    return token.status


def confirm_qr_token(
    db: Session, raw_token: str, confirming_student: Student
) -> RegistrationError | None:
    """Mark a pending, unexpired token confirmed by ``confirming_student``.

    Returns ``None`` on success, else the reason. A conditional UPDATE claims the
    token atomically, so two students scanning at once cannot both win and an
    expired/used token cannot be resurrected.
    """
    token = get_qr_token(db, raw_token)
    if token is None or token.status != QR_STATUS_PENDING:
        return RegistrationError.INVALID_TOKEN
    if effective_status(token) == QR_STATUS_EXPIRED:
        return RegistrationError.TOKEN_EXPIRED

    now = _utcnow()
    result = db.execute(
        update(RegistrationQrToken)
        .where(
            RegistrationQrToken.id == token.id,
            RegistrationQrToken.status == QR_STATUS_PENDING,
            RegistrationQrToken.expires_at > now,
        )
        .values(
            status=QR_STATUS_CONFIRMED,
            confirmed_by=confirming_student.id,
            confirmed_at=now,
        )
        # The token is already in the session (loaded above) with a naive
        # datetime read back from SQLite; let the database do the comparison
        # instead of the in-Python session evaluator, which would choke on
        # naive-vs-aware.
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return RegistrationError.INVALID_TOKEN
    db.commit()
    return None


def complete_qr_registration(
    db: Session, raw_token: str, password: str
) -> RegistrationResult:
    """Set a password for a confirmed token and create the student.

    The token must already be ``confirmed``; on success it becomes ``used``.
    """
    if len(password) < settings.MIN_PASSWORD_LENGTH:
        return RegistrationResult(
            ok=False, reason=RegistrationError.PASSWORD_TOO_SHORT
        )
    token = get_qr_token(db, raw_token)
    if token is None:
        return RegistrationResult(ok=False, reason=RegistrationError.INVALID_TOKEN)
    if token.status != QR_STATUS_CONFIRMED:
        return RegistrationResult(ok=False, reason=RegistrationError.NOT_CONFIRMED)
    if _email_taken(db, token.email):
        return RegistrationResult(ok=False, reason=RegistrationError.EMAIL_TAKEN)

    # Create the student first: only once the account exists is the token spent,
    # so a failed insert does not burn a confirmed code.
    student = _create_student(db, email=token.email, password=password)
    if student is None:
        return RegistrationResult(ok=False, reason=RegistrationError.EMAIL_TAKEN)
    db.execute(
        update(RegistrationQrToken)
        .where(RegistrationQrToken.id == token.id)
        .values(status=QR_STATUS_USED)
    )
    db.commit()
    return RegistrationResult(ok=True, student=student)


def purge_expired_tokens(db: Session, older_than_hours: int = 48) -> None:
    """Delete long-expired tokens (housekeeping helper; safe to call anytime)."""
    cutoff = _utcnow() - timedelta(hours=older_than_hours)
    db.execute(
        delete(RegistrationQrToken)
        .where(RegistrationQrToken.expires_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    db.commit()
