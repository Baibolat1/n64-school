"""Student session handling via a signed cookie (no server-side session table).

Deliberate trade-off: because there is no ``student_sessions`` table, the
session is a self-contained signed value and cannot be revoked server-side --
``logout`` only clears the cookie on the client. This is acceptable because the
student's existence and ``is_banned`` flag are re-checked against the database
on every request (see :func:`resolve_student_session` and
``app.core.deps.require_not_banned``), so a ban takes effect immediately even
if a stale cookie is still presented.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.models.student import Student

# Distinct salt so student cookies cannot be confused with any other value
# signed with the same SECRET_KEY.
_SALT = "student-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_SALT)


def issue_student_session(student: Student) -> str:
    """Return the signed cookie value for ``student`` (includes issue time)."""
    return _serializer().dumps({"student_id": student.id})


def resolve_student_session(db: Session, token: str) -> Student | None:
    """Return the student identified by a valid session token, or ``None``.

    Verifies the signature and age (``STUDENT_SESSION_TTL_DAYS``), then loads
    the student from the database so that existence and the current
    ``is_banned`` value are always fresh -- the signature alone is never
    trusted. A banned student is still returned here (with ``is_banned`` set);
    enforcing the ban is the job of ``require_not_banned`` so the caller can
    render a 403 page instead of a login redirect.
    """
    if not token:
        return None
    try:
        data = _serializer().loads(
            token, max_age=settings.STUDENT_SESSION_TTL_DAYS * 86400
        )
    except (BadSignature, SignatureExpired):
        return None

    student_id = data.get("student_id") if isinstance(data, dict) else None
    if not isinstance(student_id, int):
        return None

    return db.get(Student, student_id)
