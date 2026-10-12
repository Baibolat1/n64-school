"""Unit tests of the moderation reader (``app.services.moderation``).

The complaint-side resolver only ever sees visible content; the moderation
reader must see everything, so these tests pin the difference directly: the
same hidden row is None to one and a full target to the other. They also hold
the two resolver maps and the two label maps together, so adding a kind of
content or a PII rule cannot silently leave the moderation queue behind.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import hash_password  # noqa: E402
from app.models.audit import (  # noqa: E402
    MODERATION_ACTIONS,
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
from app.models.content import (  # noqa: E402
    CONTENT_STATUS_DELETED,
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    CONTENT_STATUSES,
    Comment,
    Post,
)
from app.models.report import (  # noqa: E402
    REPORT_STATUS_DISMISSED,
    REPORT_STATUS_OPEN,
    REPORT_STATUS_RESOLVED,
    Report,
)
from app.models.review import Review  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.models.teacher import Teacher  # noqa: E402
from app.repositories import admins as admins_repo  # noqa: E402
from app.repositories import audit as audit_repo  # noqa: E402
from app.repositories import comments as comments_repo  # noqa: E402
from app.repositories import pii_blocked as pii_blocked_repo  # noqa: E402
from app.repositories import pii_flags as pii_flags_repo  # noqa: E402
from app.repositories import posts as posts_repo  # noqa: E402
from app.repositories import reports as reports_repo  # noqa: E402
from app.repositories import students as students_repo  # noqa: E402
from app.services import reports as reports_service  # noqa: E402
from app.services import moderation as moderation_service  # noqa: E402
from app.services.moderation import (  # noqa: E402
    _MODERATION_RESOLVERS,
    ACTION_LABELS,
    AUDIT_TARGET_LABELS,
    MAX_NOTE_LENGTH,
    RULE_LABELS,
    STATUS_LABELS,
    TARGET_LABELS,
    ModerationError,
    resolve_moderation_target,
)
from app.services.pii_filter import RULES  # noqa: E402

ALIAS = "Ученик-02"


def _make_student(session_factory, alias=ALIAS) -> int:
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        return student.id


def _make_post(
    session_factory,
    author_id,
    *,
    body="Текст поста",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        post = Post(
            author_id=author_id,
            display_alias=ALIAS,
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(post)
        db.commit()
        return post.id


def _make_comment(
    session_factory,
    post_id,
    author_id,
    *,
    body="Комментарий",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        comment = Comment(
            post_id=post_id,
            author_id=author_id,
            display_alias=ALIAS,
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(comment)
        db.commit()
        return comment.id


def _make_review(
    session_factory,
    teacher_id,
    author_id,
    *,
    comment="Отзыв",
    status=CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        review = Review(
            student_id=author_id,
            teacher_id=teacher_id,
            display_alias=ALIAS,
            is_anonymous=False,
            comment=comment,
            status=status,
        )
        db.add(review)
        db.commit()
        return review.id


def _make_teacher(session_factory, name="Тестов Учитель") -> int:
    with session_factory() as db:
        teacher = Teacher(display_name=name, subject="Физика", is_active=True)
        db.add(teacher)
        db.commit()
        return teacher.id


def _make_admin(session_factory, username="admin") -> int:
    """An admin account, created the same way ``scripts/seed_admin.py`` does."""
    with session_factory() as db:
        admin = admins_repo.create_admin(
            db, username=username, password_hash=hash_password("secret-password")
        )
        db.commit()
        return admin.id


def _make_report(
    session_factory, reporter_id, target_type, target_id, *, reason_code="spam"
) -> int:
    """An open complaint by ``reporter_id`` about the given target."""
    with session_factory() as db:
        report = reports_repo.create_report(
            db,
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            comment=None,
        )
        db.commit()
        return report.id


def _make_flag(session_factory, target_type, target_id, *, rule_code="phone") -> int:
    """An open soft-PII flag on the given target."""
    with session_factory() as db:
        flag = pii_flags_repo.create_flag(
            db,
            target_type=target_type,
            target_id=target_id,
            rule_code=rule_code,
            snippet="+7 ...",
        )
        db.commit()
        return flag.id


def _trail(db) -> set[tuple[str, str, int]]:
    """The whole moderation trail as ``{(action, target_type, target_id)}``."""
    return {
        (entry.action.action, entry.action.target_type, entry.action.target_id)
        for entry in audit_repo.list_recent(db, limit=100, offset=0)
    }


# --- The maps stay in step ---------------------------------------------------


def test_resolver_maps_agree_on_the_kinds_of_content():
    """The complaint map, the moderation map and the labels cannot drift."""
    assert set(_MODERATION_RESOLVERS) == set(TARGET_LABELS)
    assert set(_MODERATION_RESOLVERS) == set(reports_service._TARGET_RESOLVERS)


def test_rule_labels_cover_exactly_the_pii_rules():
    assert set(RULE_LABELS) == {rule.code for rule in RULES}
    # A rule whose name never reached the map would render as a bare code.
    assert all(RULE_LABELS.values())


def test_status_labels_cover_exactly_the_content_statuses():
    # A queue renders whatever status a target is in, so every status a row can
    # carry needs a Russian name -- a missing one would surface as a bare code.
    assert set(STATUS_LABELS) == set(CONTENT_STATUSES)


def test_action_labels_cover_exactly_the_moderation_actions():
    # The trail is rendered from these labels, in the object card and in the
    # audit log alike: an action added to MODERATION_ACTIONS without a name here
    # would show up as a bare code like "resolve_pii_flag".
    assert set(ACTION_LABELS) == set(MODERATION_ACTIONS)


def test_audit_target_labels_cover_every_kind_of_target():
    # The audit journal names the subject of every trail row, so its map has to
    # hold all six kinds -- content, students, complaints and flags alike.
    assert set(AUDIT_TARGET_LABELS) == {
        TARGET_POST,
        TARGET_COMMENT,
        TARGET_REVIEW,
        TARGET_STUDENT,
        TARGET_REPORT,
        TARGET_PII_FLAG,
    }


# --- What the moderator sees and the student cannot --------------------------


def test_hidden_post_resolves_for_moderation_but_not_for_reporting(
    session_factory,
):
    """The contrast this module exists for: hidden is None to one, a target to
    the other."""
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author, status=CONTENT_STATUS_HIDDEN)

    with session_factory() as db:
        assert (
            reports_service.resolve_report_target(db, "post", post_id) is None
        )
        target = resolve_moderation_target(db, "post", post_id)

    assert target is not None
    assert target.target_type == "post"
    assert target.target_id == post_id
    assert target.author_id == author
    assert target.status == CONTENT_STATUS_HIDDEN
    assert target.body == "Текст поста"
    assert target.context_url == f"/feed/posts/{post_id}"
    assert target.context_available is False


def test_comment_under_a_hidden_post_is_found_without_context(session_factory):
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author, status=CONTENT_STATUS_HIDDEN)
    comment_id = _make_comment(session_factory, post_id, author)

    with session_factory() as db:
        assert (
            reports_service.resolve_report_target(db, "comment", comment_id)
            is None
        )
        target = resolve_moderation_target(db, "comment", comment_id)

    assert target is not None
    assert target.target_type == "comment"
    assert target.body == "Комментарий"
    assert target.context_url == f"/feed/posts/{post_id}"
    assert target.context_available is False


def test_hidden_review_keeps_the_teacher_page_as_context(session_factory):
    teacher_id = _make_teacher(session_factory)
    author = _make_student(session_factory)
    review_id = _make_review(
        session_factory, teacher_id, author, status=CONTENT_STATUS_HIDDEN
    )

    with session_factory() as db:
        target = resolve_moderation_target(db, "review", review_id)

    assert target is not None
    assert target.status == CONTENT_STATUS_HIDDEN
    assert target.context_url == f"/teachers/{teacher_id}"
    # The teacher is active: the review is hidden, the page is not.
    assert target.context_available is True


def test_visible_target_has_a_reachable_context(session_factory):
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        target = resolve_moderation_target(db, "post", post_id)

    assert target is not None
    assert target.status == CONTENT_STATUS_VISIBLE
    assert target.context_available is True


# --- Nothing to look at ------------------------------------------------------


def test_unknown_type_and_missing_id_answer_none(session_factory):
    with session_factory() as db:
        assert resolve_moderation_target(db, "foobar", 1) is None
        assert resolve_moderation_target(db, "post", 999) is None
        assert resolve_moderation_target(db, "comment", 999) is None
        assert resolve_moderation_target(db, "review", 999) is None


# --- The data layer ----------------------------------------------------------


def test_record_action_stores_the_fields_and_names_the_admin(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin_id,
            action="hide",
            target_type="post",
            target_id=post_id,
            note="Травля",
        )
        db.commit()
        entries = audit_repo.list_recent(db, limit=10, offset=0)
        assert audit_repo.count_all(db) == 1

    assert len(entries) == 1
    entry = entries[0]
    assert entry.action.admin_id == admin_id
    assert entry.action.action == "hide"
    assert entry.action.target_type == "post"
    assert entry.action.target_id == post_id
    assert entry.action.note == "Травля"
    assert entry.admin_username == "admin"


def test_list_recent_returns_the_newest_action_first(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin_id,
            action="hide",
            target_type="post",
            target_id=post_id,
        )
        audit_repo.record_action(
            db,
            admin_id=admin_id,
            action="restore",
            target_type="post",
            target_id=post_id,
        )
        db.commit()
        entries = audit_repo.list_recent(db, limit=10, offset=0)

    assert [entry.action.action for entry in entries] == ["restore", "hide"]


def test_set_banned_sets_the_flag_and_reason_and_clears_it(session_factory):
    student_id = _make_student(session_factory)

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        students_repo.set_banned(db, student, banned=True, reason="Спам")
        db.commit()
        assert students_repo.count_banned(db) == 1

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is True
        assert student.banned_reason == "Спам"

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        students_repo.set_banned(db, student, banned=False)
        db.commit()

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is False
        assert student.banned_reason is None
        assert students_repo.count_banned(db) == 0


def test_set_status_hidden_removes_a_post_from_the_feed_and_its_count(
    session_factory,
):
    """The invariant the whole stage rests on: hidden means out of the feed."""
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        assert posts_repo.count_visible_posts(db) == 1

    with session_factory() as db:
        post = posts_repo.get_post(db, post_id)
        posts_repo.set_status(db, post, CONTENT_STATUS_HIDDEN)
        db.commit()

    with session_factory() as db:
        assert posts_repo.list_visible_posts(db, limit=10, offset=0) == []
        assert posts_repo.count_visible_posts(db) == 0
        # The row is still there for a moderator -- only hidden, not gone.
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_HIDDEN


def test_open_report_counts_ignore_closed_complaints(session_factory):
    author = _make_student(session_factory)
    other = _make_student(session_factory, alias="Ученик-03")
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        db.add(
            Report(
                reporter_id=author,
                target_type="post",
                target_id=post_id,
                reason_code="spam",
                status=REPORT_STATUS_OPEN,
            )
        )
        db.add(
            Report(
                reporter_id=other,
                target_type="post",
                target_id=post_id,
                reason_code="abuse",
                status=REPORT_STATUS_RESOLVED,
            )
        )
        db.commit()

    with session_factory() as db:
        assert reports_repo.open_report_counts(db) == {("post", post_id): 1}
        assert reports_repo.count_open(db) == 1
        assert len(reports_repo.list_open(db, limit=10, offset=0)) == 1


def test_open_flag_counts_ignore_closed_flags(session_factory):
    with session_factory() as db:
        pii_flags_repo.create_flag(
            db, target_type="post", target_id=7, rule_code="phone", snippet="+7 ..."
        )
        db.add(
            PiiFlag(
                target_type="post",
                target_id=7,
                rule_code="email",
                snippet="a@...",
                status=PII_FLAG_STATUS_RESOLVED,
            )
        )
        db.commit()

    with session_factory() as db:
        assert pii_flags_repo.open_flag_counts(db) == {("post", 7): 1}
        assert pii_flags_repo.count_open(db) == 1
        assert len(pii_flags_repo.list_open(db, limit=10, offset=0)) == 1


def test_closing_a_report_stamps_status_admin_and_time(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        report = reports_repo.create_report(
            db,
            reporter_id=author,
            target_type="post",
            target_id=post_id,
            reason_code="spam",
            comment=None,
        )
        db.commit()
        report_id = report.id

    with session_factory() as db:
        report = db.get(Report, report_id)
        reports_repo.close(
            db, report, status=REPORT_STATUS_RESOLVED, admin_id=admin_id
        )
        db.commit()

    with session_factory() as db:
        report = db.get(Report, report_id)
        assert report.status == REPORT_STATUS_RESOLVED
        assert report.resolved_by == admin_id
        assert report.resolved_at is not None
        # A closed complaint leaves the open queue.
        assert reports_repo.count_open(db) == 0


def test_closing_a_flag_sets_status_and_time(session_factory):
    with session_factory() as db:
        flag = pii_flags_repo.create_flag(
            db, target_type="post", target_id=1, rule_code="phone", snippet="+7 ..."
        )
        db.commit()
        flag_id = flag.id

    with session_factory() as db:
        flag = db.get(PiiFlag, flag_id)
        pii_flags_repo.close(db, flag, status=PII_FLAG_STATUS_RESOLVED)
        db.commit()

    with session_factory() as db:
        flag = db.get(PiiFlag, flag_id)
        assert flag.status == PII_FLAG_STATUS_RESOLVED
        assert flag.resolved_at is not None
        assert pii_flags_repo.count_open(db) == 0


def test_blocked_attempt_listing_carries_the_student_alias(session_factory):
    student_id = _make_student(session_factory, alias="Ученик-05")

    with session_factory() as db:
        pii_blocked_repo.record_attempt(
            db, student_id=student_id, target_type="post", rule_codes=("phone", "email")
        )
        db.commit()
        entries = pii_blocked_repo.list_recent(db, limit=10, offset=0)
        assert pii_blocked_repo.count_all(db) == 1

    assert len(entries) == 1
    assert entries[0].student_alias == "Ученик-05"
    assert entries[0].attempt.rule_codes == "phone,email"
    # No fragment of the message is stored on this row.
    assert not hasattr(entries[0].attempt, "snippet")


# --- Decisions: hiding and its side effects ----------------------------------


def test_hide_closes_open_reports_and_flags_of_the_post(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    reporter = _make_student(session_factory, alias="Ученик-06")
    post_id = _make_post(session_factory, author)
    report_id = _make_report(session_factory, reporter, "post", post_id)
    flag_id = _make_flag(session_factory, "post", post_id)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.apply_content_action(
            db,
            admin,
            target_type="post",
            target_id=post_id,
            action="hide",
            note="Травля",
        )

    assert result.ok is True
    assert result.changed is True
    assert result.reports_closed == 1
    assert result.flags_closed == 1

    with session_factory() as db:
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_HIDDEN

        report = db.get(Report, report_id)
        assert report.status == REPORT_STATUS_RESOLVED
        assert report.resolved_by == admin_id
        assert report.resolved_at is not None

        flag = db.get(PiiFlag, flag_id)
        assert flag.status == PII_FLAG_STATUS_RESOLVED
        assert flag.resolved_at is not None

        # The invariant the queue relies on: after hide, no open complaints.
        assert reports_repo.list_open_for_target(db, "post", post_id) == []

        assert _trail(db) == {
            ("hide", "post", post_id),
            ("resolve_report", "report", report_id),
            ("resolve_pii_flag", "pii_flag", flag_id),
        }


def test_repeated_hide_is_a_noop_without_a_new_trail_row(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        first = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="hide"
        )
    assert first.ok is True and first.changed is True

    with session_factory() as db:
        entries_before = audit_repo.count_all(db)
        admin = admins_repo.get_admin_by_id(db, admin_id)
        again = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="hide"
        )
        assert again.ok is True
        assert again.changed is False
        assert audit_repo.count_all(db) == entries_before


def test_delete_is_terminal_restore_refused_and_hide_does_not_downgrade(
    session_factory,
):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        deleted = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="delete"
        )
    assert deleted.ok is True and deleted.changed is True

    with session_factory() as db:
        row = posts_repo.get_post(db, post_id)
        assert row is not None
        assert row.status == CONTENT_STATUS_DELETED

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        restore = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="restore"
        )
    assert restore.ok is False
    assert restore.reason is ModerationError.NOT_RESTORABLE

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        hide = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="hide"
        )
    assert hide.ok is True
    assert hide.changed is False

    with session_factory() as db:
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_DELETED


def test_restore_returns_a_hidden_post_to_the_feed(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author, status=CONTENT_STATUS_HIDDEN)

    with session_factory() as db:
        assert posts_repo.list_visible_posts(db, limit=10, offset=0) == []

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="restore"
        )
    assert result.ok is True and result.changed is True

    with session_factory() as db:
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_VISIBLE
        visible_ids = [p.id for p in posts_repo.list_visible_posts(db, limit=10, offset=0)]
        assert visible_ids == [post_id]
        assert _trail(db) == {("restore", "post", post_id)}


def test_restore_of_a_visible_post_is_a_noop(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="restore"
        )
        assert result.ok is True
        assert result.changed is False
        assert audit_repo.count_all(db) == 0


# --- Decisions: banning the author of an object ------------------------------


def test_decision_ban_blocks_the_author_closes_complaints_not_flags(
    session_factory,
):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    reporter = _make_student(session_factory, alias="Ученик-07")
    post_id = _make_post(session_factory, author)
    comment_id = _make_comment(session_factory, post_id, author)
    report_id = _make_report(session_factory, reporter, "comment", comment_id)
    flag_id = _make_flag(session_factory, "comment", comment_id)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.decide_object(
            db,
            admin,
            target_type="comment",
            target_id=comment_id,
            decision="ban",
            note="Травля",
        )
    assert result.ok is True and result.changed is True
    assert result.reports_closed == 1

    with session_factory() as db:
        student = students_repo.get_student(db, author)
        assert student.is_banned is True
        assert student.banned_reason == "Травля"

        report = db.get(Report, report_id)
        assert report.status == REPORT_STATUS_RESOLVED

        # Ban does not touch PII flags: this one is still open.
        assert db.get(PiiFlag, flag_id).status == PII_FLAG_STATUS_OPEN

        assert _trail(db) == {
            ("ban", "student", author),
            ("resolve_report", "report", report_id),
        }

    # The ban part is idempotent; the complaints are already closed too.
    with session_factory() as db:
        entries_before = audit_repo.count_all(db)
        admin = admins_repo.get_admin_by_id(db, admin_id)
        again = moderation_service.decide_object(
            db,
            admin,
            target_type="comment",
            target_id=comment_id,
            decision="ban",
            note="Травля",
        )
        assert again.ok is True
        assert again.changed is False
        assert again.reports_closed == 0
        assert audit_repo.count_all(db) == entries_before


# --- Decisions: complaints and flags one at a time ---------------------------


def test_dismiss_report_closes_only_that_complaint(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    reporter_one = _make_student(session_factory, alias="Ученик-08")
    reporter_two = _make_student(session_factory, alias="Ученик-09")
    post_id = _make_post(session_factory, author)
    report_one = _make_report(session_factory, reporter_one, "post", post_id)
    report_two = _make_report(
        session_factory, reporter_two, "post", post_id, reason_code="abuse"
    )

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.dismiss_report(
            db, admin, report_id=report_one, note="Не нарушение"
        )
    assert result.ok is True and result.changed is True

    with session_factory() as db:
        first = db.get(Report, report_one)
        assert first.status == REPORT_STATUS_DISMISSED
        assert first.resolved_by == admin_id
        assert first.resolved_at is not None

        second = db.get(Report, report_two)
        assert second.status == REPORT_STATUS_OPEN

        assert _trail(db) == {("dismiss_report", "report", report_one)}


def test_close_flag_resolves_and_dismisses(session_factory):
    admin_id = _make_admin(session_factory)
    flag_resolved = _make_flag(session_factory, "post", 1, rule_code="phone")
    flag_dismissed = _make_flag(session_factory, "comment", 1, rule_code="email")

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        resolved = moderation_service.close_flag(
            db, admin, flag_id=flag_resolved, decision=PII_FLAG_STATUS_RESOLVED
        )
        assert resolved.ok is True and resolved.changed is True

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        dismissed = moderation_service.close_flag(
            db, admin, flag_id=flag_dismissed, decision=PII_FLAG_STATUS_DISMISSED
        )
        assert dismissed.ok is True and dismissed.changed is True

    with session_factory() as db:
        first = db.get(PiiFlag, flag_resolved)
        assert first.status == PII_FLAG_STATUS_RESOLVED
        assert first.resolved_at is not None

        second = db.get(PiiFlag, flag_dismissed)
        assert second.status == PII_FLAG_STATUS_DISMISSED
        assert second.resolved_at is not None

        assert _trail(db) == {
            ("resolve_pii_flag", "pii_flag", flag_resolved),
            ("dismiss_pii_flag", "pii_flag", flag_dismissed),
        }


# --- Decisions: banning a student directly -----------------------------------


def test_ban_and_unban_student_are_idempotent(session_factory):
    admin_id = _make_admin(session_factory)
    student_id = _make_student(session_factory)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        banned = moderation_service.ban_student(
            db, admin, student_id=student_id, note="Спам"
        )
    assert banned.ok is True and banned.changed is True

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is True
        assert student.banned_reason == "Спам"
        assert _trail(db) == {("ban", "student", student_id)}

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        again = moderation_service.ban_student(
            db, admin, student_id=student_id, note="Спам"
        )
        assert again.ok is True and again.changed is False
    with session_factory() as db:
        assert audit_repo.count_all(db) == 1

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        lifted = moderation_service.unban_student(db, admin, student_id=student_id)
    assert lifted.ok is True and lifted.changed is True

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is False
        assert student.banned_reason is None
        assert _trail(db) == {
            ("ban", "student", student_id),
            ("unban", "student", student_id),
        }

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        lifted_again = moderation_service.unban_student(db, admin, student_id=student_id)
        assert lifted_again.ok is True and lifted_again.changed is False
    with session_factory() as db:
        assert audit_repo.count_all(db) == 2


# --- Decisions: notes and refusals -------------------------------------------


def test_over_long_note_is_refused_and_changes_nothing(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.apply_content_action(
            db,
            admin,
            target_type="post",
            target_id=post_id,
            action="hide",
            note="x" * (MAX_NOTE_LENGTH + 1),
        )
    assert result.ok is False
    assert result.reason is ModerationError.TOO_LONG

    with session_factory() as db:
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_VISIBLE
        assert audit_repo.count_all(db) == 0


def test_blank_note_is_stored_as_none(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="hide", note="   "
        )
        assert result.ok is True
        entry = audit_repo.list_recent(db, limit=10, offset=0)[0]
        assert entry.action.note is None


def test_missing_targets_are_refused_and_nothing_is_recorded(session_factory):
    admin_id = _make_admin(session_factory)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        missing_target = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=999, action="hide"
        )
        assert missing_target.reason is ModerationError.TARGET_NOT_FOUND

        unknown_type = moderation_service.apply_content_action(
            db, admin, target_type="foobar", target_id=1, action="hide"
        )
        assert unknown_type.reason is ModerationError.TARGET_NOT_FOUND

        missing_report = moderation_service.dismiss_report(db, admin, report_id=999)
        assert missing_report.reason is ModerationError.REPORT_NOT_FOUND

        missing_flag = moderation_service.close_flag(
            db, admin, flag_id=999, decision=PII_FLAG_STATUS_RESOLVED
        )
        assert missing_flag.reason is ModerationError.FLAG_NOT_FOUND

        missing_student = moderation_service.ban_student(db, admin, student_id=999)
        assert missing_student.reason is ModerationError.STUDENT_NOT_FOUND

        missing_unban = moderation_service.unban_student(db, admin, student_id=999)
        assert missing_unban.reason is ModerationError.STUDENT_NOT_FOUND

        assert audit_repo.count_all(db) == 0


def test_unknown_actions_are_refused(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        bad_action = moderation_service.apply_content_action(
            db, admin, target_type="post", target_id=post_id, action="ban"
        )
        assert bad_action.reason is ModerationError.UNKNOWN_ACTION

        bad_decision = moderation_service.decide_object(
            db, admin, target_type="post", target_id=post_id, decision="restore"
        )
        assert bad_decision.reason is ModerationError.UNKNOWN_ACTION

        assert audit_repo.count_all(db) == 0
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_VISIBLE


def test_hide_on_a_comment_leaves_a_visible_post_alone(session_factory):
    admin_id = _make_admin(session_factory)
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author)
    comment_id = _make_comment(session_factory, post_id, author)

    with session_factory() as db:
        admin = admins_repo.get_admin_by_id(db, admin_id)
        result = moderation_service.apply_content_action(
            db, admin, target_type="comment", target_id=comment_id, action="hide"
        )
        assert result.ok is True
        assert result.changed is True

    with session_factory() as db:
        assert comments_repo.get_comment(db, comment_id).status == CONTENT_STATUS_HIDDEN
        # Only the comment is touched; its post stays visible.
        assert posts_repo.get_post(db, post_id).status == CONTENT_STATUS_VISIBLE
