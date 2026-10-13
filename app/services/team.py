"""Applications to the platform's development team.

A student who wants to help build the site picks a role and leaves a way to
reach them; the row is written here and read by an admin. Small module, but the
rules it holds are the interesting part:

* **The role is a code, the label is presentation.** The form offers four
  choices and the database stores ``frontend``, ``backend``, ``design`` or
  ``cyber``; ``ROLE_LABELS`` is what a page renders. Same split as the school
  pages: adding a fifth role is a line here and a line in the template, and
  nothing in between has to learn a new word.
* **The handle is validated by shape, not by fetching anything.** The site has no
  network clients for this and no business talking to Telegram; it checks that
  the value could be a handle and stores it.
* **One row per student.** Applying again replaces the previous answer rather
  than piling up rows: the student's latest word is what the team needs.

Transaction policy (mirrors the other service modules): this layer owns commits
-- :func:`submit_application` commits.
"""

import re
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.student import Student
from app.models.team import DevApplication

# The four roles, in the order the form offers them. Ours, not the form's: the
# template renders these and never spells one out itself.
ROLES: tuple[str, ...] = ("frontend", "backend", "design", "cyber")

ROLE_LABELS: dict[str, str] = {
    "frontend": "Frontend",
    "backend": "Backend",
    "design": "Дизайн",
    "cyber": "Киберспорт",
}

# Upper bound on the handle as typed, before normalisation. Rendered as
# maxlength on the form and checked before anything else touches the value.
MAX_CONTACT_LENGTH = 64

# "@name" or "name", where name is what Telegram allows: letters, digits and
# underscores, 5 to 32 of them. A full "t.me/name" link is accepted too and
# reduced to the handle -- a student who copies the link out of their profile
# should not be told they are wrong.
_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_]{5,32})$")
_LINK_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,32})/?$")


class TeamError(Enum):
    """Why an application was refused.

    A closed set, so the router maps a reason to a sentence and no message
    string lives in here.
    """

    UNKNOWN_ROLE = "unknown_role"
    EMPTY_CONTACT = "empty_contact"
    BAD_CONTACT = "bad_contact"
    TOO_LONG = "too_long"


@dataclass(frozen=True)
class ApplicationResult:
    """Outcome of an application attempt.

    ``application`` is the stored row on success, and ``contact`` always carries
    the submitted handle back so a refused form can echo it rather than making
    the student type it again.
    """

    ok: bool
    application: DevApplication | None = None
    reason: TeamError | None = None
    contact: str = ""


def normalize_contact(raw: str) -> str | None:
    """Return the handle in its one stored shape (``@name``), or None.

    Handles are stored with the ``@`` even though Telegram does not require it
    when typed: one shape means the admin page, the search for a student and any
    future comparison all see the same string.
    """
    value = (raw or "").strip()
    if not value:
        return None
    link = _LINK_RE.match(value)
    if link:
        return "@" + link.group(1)
    handle = _HANDLE_RE.match(value)
    if handle:
        return "@" + handle.group(1)
    return None


def role_label(code: str) -> str:
    """Human-readable name of a role.

    An unknown code is shown as it is rather than as an error: a role removed
    from ``ROLES`` after somebody applied for it must not blank out the row that
    is already in the database.
    """
    return ROLE_LABELS.get(code, code)


def refusal_message(result: ApplicationResult) -> str:
    """Human-readable sentence for a refused application.

    Here rather than in a router because two of them refuse the same form --
    the page (/me/dev-applications) and the async endpoint the script uses (see
    app.routers.api) -- and a student deserves the same sentence from both.
    """
    if result.reason is TeamError.UNKNOWN_ROLE:
        return "Выберите роль"
    if result.reason is TeamError.EMPTY_CONTACT:
        return "Укажите ник в Telegram"
    if result.reason is TeamError.BAD_CONTACT:
        return "Ник выглядит не так: например @nickname или ссылка t.me/nickname"
    if result.reason is TeamError.TOO_LONG:
        return "Слишком длинный ник"
    return "Не удалось отправить заявку"


def get_for_student(db: Session, student_id: int) -> DevApplication | None:
    """The student's application, if they have one."""
    return db.execute(
        select(DevApplication).where(DevApplication.student_id == student_id)
    ).scalar_one_or_none()


def list_applications(db: Session) -> list[DevApplication]:
    """Every application, newest first. For the admin page."""
    return list(
        db.execute(
            select(DevApplication).order_by(DevApplication.updated_at.desc())
        ).scalars()
    )


def count_applications(db: Session) -> int:
    """How many applications are waiting. For the admin dashboard."""
    return db.execute(
        select(func.count()).select_from(DevApplication)
    ).scalar_one()


def submit_application(
    db: Session, student: Student, role: str, contact: str
) -> ApplicationResult:
    """Store (or replace) the student's application.

    Validation is all here rather than in the form: the same function backs the
    page and the JSON endpoint, and a rule that lived in one of them would be
    missing from the other.
    """
    role = (role or "").strip()
    if role not in ROLES:
        return ApplicationResult(False, reason=TeamError.UNKNOWN_ROLE, contact=contact)

    if len(contact) > MAX_CONTACT_LENGTH:
        return ApplicationResult(False, reason=TeamError.TOO_LONG, contact=contact)

    if not contact.strip():
        return ApplicationResult(False, reason=TeamError.EMPTY_CONTACT, contact=contact)

    normalized = normalize_contact(contact)
    if normalized is None:
        return ApplicationResult(
            False, reason=TeamError.BAD_CONTACT, contact=contact.strip()
        )

    existing = get_for_student(db, student.id)
    if existing is None:
        existing = DevApplication(
            student_id=student.id, role=role, contact=normalized
        )
        db.add(existing)
    else:
        existing.role = role
        existing.contact = normalized

    try:
        db.commit()
    except IntegrityError:
        # Two submissions of the same student raced and both took the insert
        # branch: the unique constraint refused the second, so the row the
        # winner wrote is now the student's application. Roll back and read it.
        db.rollback()
        existing = get_for_student(db, student.id)
        if existing is None:
            raise
        existing.role = role
        existing.contact = normalized
        db.commit()

    db.refresh(existing)
    return ApplicationResult(True, application=existing)
