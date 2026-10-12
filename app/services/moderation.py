"""The moderation reader: what a target looks like to a moderator.

The complaint-side resolver (:mod:`app.services.reports`) deliberately sees
only *visible* content, so a student cannot use the report endpoint to probe
what a moderator has hidden. A moderator needs the opposite: to judge a
complaint they must read the post or comment as it stands, including one that
is already hidden or deleted, and to undo a mistake they must be able to reach
it at all. This module is that second reader -- same three kinds of target, no
visibility filter anywhere.

What it returns is a snapshot, not the ORM row: :class:`ModerationTarget` carries
only what the queue needs to render, plus two pieces of judgement:

* ``status`` is reported as-is, whatever it is -- the caller decides what a
  hidden or deleted target means.
* ``context_available`` says whether the public page holding the content really
  opens for a student. A hidden or deleted post 404s the feed; a comment under
  such a post is unreachable with it; a review is shown on its teacher's page,
  which exists only while the teacher is active (a hidden review keeps that page,
  only the review itself disappears from it).

The label maps live here too: :data:`TARGET_LABELS` names the kinds of content
and :data:`RULE_LABELS` gives a short human name to every PII rule code, so the
queue can render both without repeating the vocabulary.

The second half of the module is the writer that pairs with this reader: a
moderator's decisions (hide, restore, delete, ban, dismissing a complaint or a
PII flag) are applied here, and this layer owns the transaction -- every public
function commits, exactly like the other service modules. Two rules shape what a
decision does beyond its target:

* A decision on a piece of content closes every open complaint about it (and
  every open PII flag, since a hidden or deleted target leaves a flag with
  nothing left to show).
* Deletion is terminal. ``restore`` only ever lifts ``hidden``; a deleted row
  cannot be brought back, or ``delete`` and ``hide`` would mean the same thing.

A repeated decision changes nothing and writes no trail row, but it is not an
error either.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from sqlalchemy.orm import Session

from app.models.admin import Admin
from app.models.audit import (
    ACTION_BAN,
    ACTION_DELETE,
    ACTION_DISMISS_PII_FLAG,
    ACTION_DISMISS_REPORT,
    ACTION_HIDE,
    ACTION_RESOLVE_PII_FLAG,
    ACTION_RESOLVE_REPORT,
    ACTION_RESTORE,
    ACTION_UNBAN,
    PII_FLAG_STATUS_DISMISSED,
    PII_FLAG_STATUS_OPEN,
    PII_FLAG_STATUS_RESOLVED,
    TARGET_COMMENT,
    TARGET_PII_FLAG,
    TARGET_POST,
    TARGET_REPORT,
    TARGET_REVIEW,
    TARGET_STUDENT,
    PiiFlag,
)
from app.models.content import (
    CONTENT_STATUS_DELETED,
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
)
from app.models.report import (
    REPORT_STATUS_DISMISSED,
    REPORT_STATUS_OPEN,
    REPORT_STATUS_RESOLVED,
    Report,
)
from app.repositories import audit as audit_repo
from app.repositories import comments as comments_repo
from app.repositories import pii_flags as pii_flags_repo
from app.repositories import posts as posts_repo
from app.repositories import reports as reports_repo
from app.repositories import reviews as reviews_repo
from app.repositories import students as students_repo
from app.repositories import teachers as teachers_repo


@dataclass(frozen=True)
class ModerationTarget:
    """A complaint's subject as a moderator sees it.

    Unlike ``app.services.reports.ReportTarget`` this is built without any
    visibility filter, so a hidden or deleted target resolves here where the
    complaint resolver would answer None. ``body`` is the post/comment text or
    the review's comment; ``context_url`` is the public page where the content
    lives, and ``context_available`` whether that page actually opens.
    """

    target_type: str
    target_id: int
    author_id: int
    display_alias: str
    is_anonymous: bool
    status: str
    body: str
    context_url: str
    context_available: bool


def _resolve_post(db: Session, target_id: int) -> ModerationTarget | None:
    post = posts_repo.get_post(db, target_id)
    if post is None:
        return None
    return ModerationTarget(
        target_type=TARGET_POST,
        target_id=post.id,
        author_id=post.author_id,
        display_alias=post.display_alias,
        is_anonymous=post.is_anonymous,
        status=post.status,
        body=post.body,
        context_url=f"/feed/posts/{post.id}",
        # The feed 404s anything that is not visible, so a hidden or deleted
        # post has no page a moderator could be sent to.
        context_available=post.status == CONTENT_STATUS_VISIBLE,
    )


def _resolve_comment(db: Session, target_id: int) -> ModerationTarget | None:
    comment = comments_repo.get_comment(db, target_id)
    if comment is None:
        return None
    post = posts_repo.get_post(db, comment.post_id)
    return ModerationTarget(
        target_type=TARGET_COMMENT,
        target_id=comment.id,
        author_id=comment.author_id,
        display_alias=comment.display_alias,
        is_anonymous=comment.is_anonymous,
        status=comment.status,
        body=comment.body,
        context_url=f"/feed/posts/{comment.post_id}",
        # A comment lives on its post's page: if that post is gone or hidden,
        # the comment is unreachable even when the comment row itself is visible.
        context_available=post is not None
        and post.status == CONTENT_STATUS_VISIBLE,
    )


def _resolve_review(db: Session, target_id: int) -> ModerationTarget | None:
    review = reviews_repo.get_review_by_id(db, target_id)
    if review is None:
        return None
    teacher = teachers_repo.get_teacher(db, review.teacher_id)
    return ModerationTarget(
        target_type=TARGET_REVIEW,
        target_id=review.id,
        author_id=review.student_id,
        display_alias=review.display_alias,
        is_anonymous=review.is_anonymous,
        status=review.status,
        body=review.comment,
        context_url=f"/teachers/{review.teacher_id}",
        # The teacher's page exists while the teacher is active; hiding the
        # review removes it from that page but leaves the page itself reachable.
        context_available=teacher is not None and teacher.is_active,
    )


# Which kinds of content the moderation queue can show, and how each is found
# without a visibility filter. Keyed by the same codes the moderation trail uses
# (``app.models.audit``). The complaint-side map (``app.services.reports``) must
# carry the same keys -- ``tests/test_admin_moderation.py`` fails if they drift.
_MODERATION_RESOLVERS: dict[str, Callable[[Session, int], ModerationTarget | None]] = {
    TARGET_POST: _resolve_post,
    TARGET_COMMENT: _resolve_comment,
    TARGET_REVIEW: _resolve_review,
}

# Short human names for the kinds of content, in the same key set and order as
# the resolvers above so a queue can render a target type without spelling it out.
TARGET_LABELS: dict[str, str] = {
    TARGET_POST: "Пост",
    TARGET_COMMENT: "Комментарий",
    TARGET_REVIEW: "Отзыв",
}

# Short human names for every kind of target the trail can point at, so the
# audit journal can name a record's subject without spelling out its code. This
# is deliberately separate from :data:`TARGET_LABELS`: that map names only the
# content kinds that have a hub and its key set is pinned to the resolvers,
# while a trail row can also point at a student, a complaint or a PII flag.
AUDIT_TARGET_LABELS: dict[str, str] = {
    TARGET_POST: "Пост",
    TARGET_COMMENT: "Комментарий",
    TARGET_REVIEW: "Отзыв",
    TARGET_STUDENT: "Ученик",
    TARGET_REPORT: "Жалоба",
    TARGET_PII_FLAG: "Флаг ПДн",
}

# Short human names for the PII filter's rule codes (see
# ``app.services.pii_filter.RULES``): the code identifies a rule, the label is
# what the queue shows. The long descriptions in that table stay where they are;
# a code with no label here would be shown raw, so the two must agree
# (``tests/test_admin_moderation.py`` checks it).
RULE_LABELS: dict[str, str] = {
    "phone": "Телефон",
    "email": "E-mail",
    "link": "Ссылка",
    "messenger": "Мессенджер",
    "full_name": "ФИО",
    "address": "Адрес",
    "class_group": "Класс или группа",
}

# Short human names for a target's status, so the queue can show whether a hidden
# or deleted target was already dealt with instead of looking untouched. The keys
# are the content status codes (``app.models.content``); "pending" has no named
# constant yet, so it is spelled out here exactly as ``CONTENT_STATUSES`` holds
# it.
STATUS_LABELS: dict[str, str] = {
    CONTENT_STATUS_VISIBLE: "Опубликовано",
    "pending": "На проверке",
    CONTENT_STATUS_HIDDEN: "Скрыто",
    CONTENT_STATUS_DELETED: "Удалено",
}

# Short human names for the actions in the moderation trail, so a screen can show
# what a moderator did instead of the raw code. The keys are exactly
# ``MODERATION_ACTIONS`` (``app.models.audit``); stage 11 reuses this map for the
# audit journal, so every action needs a name here.
ACTION_LABELS: dict[str, str] = {
    ACTION_HIDE: "Скрыто",
    ACTION_RESTORE: "Возвращено в ленту",
    ACTION_DELETE: "Удалено",
    ACTION_BAN: "Автор заблокирован",
    ACTION_UNBAN: "Автор разблокирован",
    ACTION_DISMISS_REPORT: "Жалоба отклонена",
    ACTION_RESOLVE_REPORT: "Жалоба закрыта решением",
    ACTION_RESOLVE_PII_FLAG: "Флаг ПДн закрыт решением",
    ACTION_DISMISS_PII_FLAG: "Флаг ПДн отклонён",
}


def resolve_moderation_target(
    db: Session, target_type: str, target_id: int
) -> ModerationTarget | None:
    """Find what a moderator is looking at, whatever its status.

    Returns None only when the type is unknown or the row does not exist:
    hidden and deleted content resolves exactly like visible content, so the
    queue can read and un-hide it.
    """
    resolver = _MODERATION_RESOLVERS.get(target_type)
    if resolver is None:
        return None
    return resolver(db, target_id)


# --- Decisions: the write side of the reader above ---------------------------

# How long a moderator's note may be. A note is the explanation attached to a
# decision, not a comment thread of its own.
MAX_NOTE_LENGTH = 500

# Every action :func:`apply_content_action` accepts, and the subset that takes
# content away (:func:`decide_object` accepts these too -- it has no ``restore``).
_CONTENT_ACTIONS = frozenset({ACTION_HIDE, ACTION_RESTORE, ACTION_DELETE})
_REMOVING_ACTIONS = frozenset({ACTION_HIDE, ACTION_DELETE})


class ModerationError(str, Enum):
    """Machine-readable reasons a moderation decision can be refused."""

    TARGET_NOT_FOUND = "target_not_found"
    REPORT_NOT_FOUND = "report_not_found"
    FLAG_NOT_FOUND = "flag_not_found"
    STUDENT_NOT_FOUND = "student_not_found"
    NOT_RESTORABLE = "not_restorable"
    UNKNOWN_ACTION = "unknown_action"
    TOO_LONG = "too_long"


@dataclass(frozen=True)
class ActionResult:
    """Outcome of one moderation decision.

    ``changed`` is False when the target was already in the requested state: a
    repeated ``hide`` or ``ban`` is not an error, it is a no-op that writes no
    trail row. ``reports_closed`` and ``flags_closed`` count what the decision
    swept up alongside the target itself.
    """

    ok: bool
    changed: bool = False
    reason: ModerationError | None = None
    reports_closed: int = 0
    flags_closed: int = 0


def _clean_note(note: str | None) -> tuple[str | None, ModerationError | None]:
    """Trim a moderator's note; a blank one becomes None, an over-long one refused.

    Runs at every entry point before anything is written, so both rules (strip,
    length) live in exactly one place.
    """
    if note is None:
        return None, None
    cleaned = note.strip()
    if not cleaned:
        return None, None
    if len(cleaned) > MAX_NOTE_LENGTH:
        return None, ModerationError.TOO_LONG
    return cleaned, None


def _set_target_status(
    db: Session, target_type: str, target_id: int, status: str
) -> None:
    """Write ``status`` onto the target's own row.

    :func:`resolve_moderation_target` hands back a snapshot, but the setters need
    the ORM row; the target was already resolved, so the row exists.
    """
    if target_type == TARGET_POST:
        posts_repo.set_status(db, posts_repo.get_post(db, target_id), status)
    elif target_type == TARGET_COMMENT:
        comments_repo.set_status(db, comments_repo.get_comment(db, target_id), status)
    elif target_type == TARGET_REVIEW:
        reviews_repo.set_status(
            db, reviews_repo.get_review_by_id(db, target_id), status
        )


def _resolve_open_reports(
    db: Session, admin: Admin, target_type: str, target_id: int, note: str | None
) -> int:
    """Close every open complaint about the target as resolved; returns the count.

    One row per complaint: the complaint is closed with the admin and the time,
    and the trail gets its own ``resolve_report`` entry pointed at the complaint,
    so the decision, its note and each closure can be told apart.
    """
    reports = reports_repo.list_open_for_target(db, target_type, target_id)
    for report in reports:
        reports_repo.close(
            db, report, status=REPORT_STATUS_RESOLVED, admin_id=admin.id
        )
        audit_repo.record_action(
            db,
            admin_id=admin.id,
            action=ACTION_RESOLVE_REPORT,
            target_type=TARGET_REPORT,
            target_id=report.id,
            note=note,
        )
    return len(reports)


def _resolve_open_flags(
    db: Session, admin: Admin, target_type: str, target_id: int, note: str | None
) -> int:
    """Close every open PII flag on the target as resolved; returns the count.

    A hidden or deleted target leaves its flags with nothing to show, so they are
    swept up with the same decision rather than left to mock a moderator. Each
    closure gets its own ``resolve_pii_flag`` trail row.
    """
    flags = pii_flags_repo.list_open_for_target(db, target_type, target_id)
    for flag in flags:
        pii_flags_repo.close(db, flag, status=PII_FLAG_STATUS_RESOLVED)
        audit_repo.record_action(
            db,
            admin_id=admin.id,
            action=ACTION_RESOLVE_PII_FLAG,
            target_type=TARGET_PII_FLAG,
            target_id=flag.id,
            note=note,
        )
    return len(flags)


def apply_content_action(
    db: Session,
    admin: Admin,
    *,
    target_type: str,
    target_id: int,
    action: str,
    note: str | None = None,
) -> ActionResult:
    """Hide, restore or delete one piece of content, as a moderator decided.

    ``restore`` only ever lifts a ``hidden`` target: deletion is terminal, so a
    deleted row answers :attr:`ModerationError.NOT_RESTORABLE` and a visible one
    is a no-op. A real ``hide`` or ``delete`` also closes every open complaint
    about the target (as resolved) and every open PII flag on it, each with its
    own trail row. The admin comes from the session, never from a form.
    """
    if action not in _CONTENT_ACTIONS:
        return ActionResult(ok=False, reason=ModerationError.UNKNOWN_ACTION)
    note, error = _clean_note(note)
    if error is not None:
        return ActionResult(ok=False, reason=error)

    target = resolve_moderation_target(db, target_type, target_id)
    if target is None:
        return ActionResult(ok=False, reason=ModerationError.TARGET_NOT_FOUND)

    if action == ACTION_RESTORE:
        if target.status == CONTENT_STATUS_DELETED:
            return ActionResult(ok=False, reason=ModerationError.NOT_RESTORABLE)
        if target.status != CONTENT_STATUS_HIDDEN:
            return ActionResult(ok=True, changed=False)
        new_status = CONTENT_STATUS_VISIBLE
    elif action == ACTION_HIDE:
        # Deletion is not downgraded to hiding: an already deleted target stays
        # deleted, and an already hidden one is simply already done.
        if target.status in (CONTENT_STATUS_HIDDEN, CONTENT_STATUS_DELETED):
            return ActionResult(ok=True, changed=False)
        new_status = CONTENT_STATUS_HIDDEN
    else:  # ACTION_DELETE
        if target.status == CONTENT_STATUS_DELETED:
            return ActionResult(ok=True, changed=False)
        new_status = CONTENT_STATUS_DELETED

    _set_target_status(db, target_type, target_id, new_status)
    audit_repo.record_action(
        db,
        admin_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        note=note,
    )

    reports_closed = 0
    flags_closed = 0
    if action in _REMOVING_ACTIONS:
        reports_closed = _resolve_open_reports(db, admin, target_type, target_id, note)
        flags_closed = _resolve_open_flags(db, admin, target_type, target_id, note)

    db.commit()
    return ActionResult(
        ok=True,
        changed=True,
        reports_closed=reports_closed,
        flags_closed=flags_closed,
    )


def decide_object(
    db: Session,
    admin: Admin,
    *,
    target_type: str,
    target_id: int,
    decision: str,
    note: str | None = None,
) -> ActionResult:
    """Apply a moderator's verdict on a piece of content.

    ``hide`` and ``delete`` are exactly :func:`apply_content_action`, with the
    same cleanup of complaints and flags. ``ban`` blocks the target's author
    instead of touching the content: it writes its own ``ban`` trail row against
    the student and closes the object's open complaints, but leaves its PII flags
    alone. Banning an already banned author changes nothing, yet the complaints
    are brought in anyway, so a second verdict is never lost.
    """
    if decision in _REMOVING_ACTIONS:
        return apply_content_action(
            db,
            admin,
            target_type=target_type,
            target_id=target_id,
            action=decision,
            note=note,
        )
    if decision != ACTION_BAN:
        return ActionResult(ok=False, reason=ModerationError.UNKNOWN_ACTION)

    note, error = _clean_note(note)
    if error is not None:
        return ActionResult(ok=False, reason=error)

    target = resolve_moderation_target(db, target_type, target_id)
    if target is None:
        return ActionResult(ok=False, reason=ModerationError.TARGET_NOT_FOUND)

    student = students_repo.get_student(db, target.author_id)
    if student is None:
        return ActionResult(ok=False, reason=ModerationError.STUDENT_NOT_FOUND)

    changed = not student.is_banned
    if changed:
        students_repo.set_banned(db, student, banned=True, reason=note)
        audit_repo.record_action(
            db,
            admin_id=admin.id,
            action=ACTION_BAN,
            target_type=TARGET_STUDENT,
            target_id=student.id,
            note=note,
        )

    reports_closed = _resolve_open_reports(db, admin, target_type, target_id, note)
    db.commit()
    return ActionResult(ok=True, changed=changed, reports_closed=reports_closed)


def dismiss_report(
    db: Session, admin: Admin, *, report_id: int, note: str | None = None
) -> ActionResult:
    """Dismiss one complaint without touching the content it is about.

    Only a still-open complaint can be dismissed; a closed or missing one answers
    :attr:`ModerationError.REPORT_NOT_FOUND`, so the same complaint cannot be
    closed twice and no other complaint about the target is affected.
    """
    note, error = _clean_note(note)
    if error is not None:
        return ActionResult(ok=False, reason=error)

    report = db.get(Report, report_id)
    if report is None or report.status != REPORT_STATUS_OPEN:
        return ActionResult(ok=False, reason=ModerationError.REPORT_NOT_FOUND)

    reports_repo.close(db, report, status=REPORT_STATUS_DISMISSED, admin_id=admin.id)
    audit_repo.record_action(
        db,
        admin_id=admin.id,
        action=ACTION_DISMISS_REPORT,
        target_type=TARGET_REPORT,
        target_id=report.id,
        note=note,
    )
    db.commit()
    return ActionResult(ok=True, changed=True)


def close_flag(
    db: Session,
    admin: Admin,
    *,
    flag_id: int,
    decision: str,
    note: str | None = None,
) -> ActionResult:
    """Resolve or dismiss one open PII flag.

    ``decision`` is the flag's own new status (:data:`PII_FLAG_STATUS_RESOLVED` or
    :data:`PII_FLAG_STATUS_DISMISSED`); the trail action follows from it. Only an
    open flag can be closed, so a second attempt answers
    :attr:`ModerationError.FLAG_NOT_FOUND`.
    """
    note, error = _clean_note(note)
    if error is not None:
        return ActionResult(ok=False, reason=error)

    if decision == PII_FLAG_STATUS_RESOLVED:
        action = ACTION_RESOLVE_PII_FLAG
    elif decision == PII_FLAG_STATUS_DISMISSED:
        action = ACTION_DISMISS_PII_FLAG
    else:
        return ActionResult(ok=False, reason=ModerationError.UNKNOWN_ACTION)

    flag = db.get(PiiFlag, flag_id)
    if flag is None or flag.status != PII_FLAG_STATUS_OPEN:
        return ActionResult(ok=False, reason=ModerationError.FLAG_NOT_FOUND)

    pii_flags_repo.close(db, flag, status=decision)
    audit_repo.record_action(
        db,
        admin_id=admin.id,
        action=action,
        target_type=TARGET_PII_FLAG,
        target_id=flag.id,
        note=note,
    )
    db.commit()
    return ActionResult(ok=True, changed=True)


def ban_student(
    db: Session, admin: Admin, *, student_id: int, note: str | None = None
) -> ActionResult:
    """Ban a student, recording ``note`` as the reason.

    Banning an already banned student is a no-op: nothing is written and no new
    trail row appears.
    """
    return _set_student_ban(
        db, admin, student_id=student_id, banned=True, note=note, action=ACTION_BAN
    )


def unban_student(
    db: Session, admin: Admin, *, student_id: int, note: str | None = None
) -> ActionResult:
    """Lift a student's ban (the repository clears the stored reason with it)."""
    return _set_student_ban(
        db,
        admin,
        student_id=student_id,
        banned=False,
        note=note,
        action=ACTION_UNBAN,
    )


def _set_student_ban(
    db: Session,
    admin: Admin,
    *,
    student_id: int,
    banned: bool,
    note: str | None,
    action: str,
) -> ActionResult:
    """Shared body of :func:`ban_student` and :func:`unban_student`.

    A ban taken here names the student and nothing else, so it closes no
    complaints: whatever the student published stays in the feed, and whoever
    complained about it is still waiting for a verdict on that object. Banning
    from an object's card is the other case -- there the ban is the verdict on
    that object, which is why ``decide_object`` closes the complaints on it.
    """
    note, error = _clean_note(note)
    if error is not None:
        return ActionResult(ok=False, reason=error)

    student = students_repo.get_student(db, student_id)
    if student is None:
        return ActionResult(ok=False, reason=ModerationError.STUDENT_NOT_FOUND)

    if student.is_banned == banned:
        return ActionResult(ok=True, changed=False)

    students_repo.set_banned(db, student, banned=banned, reason=note)
    audit_repo.record_action(
        db,
        admin_id=admin.id,
        action=action,
        target_type=TARGET_STUDENT,
        target_id=student.id,
        note=note,
    )
    db.commit()
    return ActionResult(ok=True, changed=True)
