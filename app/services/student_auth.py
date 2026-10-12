"""Student session handling via a signed cookie (no server-side session table).

Deliberate trade-off: because there is no ``student_sessions`` table, the
session is a self-contained signed value and cannot be revoked server-side --
``logout`` only clears the cookie on the client. This is acceptable because the
student's existence and ``is_banned`` flag are re-checked against the database
on every request (see :func:`resolve_student_session` and
``app.core.deps.require_not_banned``), so a ban takes effect immediately even
if a stale cookie is still presented.
"""

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import verify_password
from app.models.student import Student

# Distinct salt so student cookies cannot be confused with any other value
# signed with the same SECRET_KEY.
_SALT = "student-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_SALT)


def issue_student_session(student: Student) -> str:
    """Return the signed cookie value for ``student`` (includes issue time)."""
    return _serializer().dumps({"student_id": student.id})


def login_with_password(db: Session, email: str, password: str) -> Student | None:
    """Authenticate a self-registered student by email and password.

    Returns the student on success, or ``None`` for every failure cause (no such
    email, no password set, wrong password) so the form cannot be used to probe
    which addresses have accounts. A banned student is still returned; the ban
    is enforced downstream by ``require_not_banned``, as for invite logins.
    """
    student = db.execute(
        select(Student).where(Student.email == email)
    ).scalar_one_or_none()
    if student is None or student.password_hash is None:
        return None
    if not verify_password(student.password_hash, password):
        return None
    return student


def resolve_student_session(db: Session, token: str) -> Student | None:
    """Return the student identified by a valid session token, or ``None``.

    The token's age is deliberately *not* checked: a student session lasts
    until the student logs out, so a signed value issued years ago is still
    accepted. ``loads`` is called without an age limit on purpose (the
    ``STUDENT_SESSION_COOKIE_DAYS`` setting only limits how long the browser
    stores the cookie, not how long the token is valid). This is a conscious
    trade-off, not an oversight. The signature is still verified; existence and
    the current ``is_banned`` flag are re-read from the database on every
    request, so a ban takes effect immediately. There is no server-side
    revocation: the only ways to cut a session short are banning the student or
    changing ``SECRET_KEY`` (which logs every student out at once). A banned
    student is still returned here (with ``is_banned`` set); enforcing the ban
    is the job of ``require_not_banned`` so the caller can render a 403 page
    instead of a login redirect.
    """
    if not token:
        return None
    try:
        data = _serializer().loads(token)
    except BadSignature:
        return None

    student_id = data.get("student_id") if isinstance(data, dict) else None
    if not isinstance(student_id, int):
        return None

    return db.get(Student, student_id)
