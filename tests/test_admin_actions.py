"""The moderator's POST actions on the object hub (stage 12, part 2).

The hub (``tests/test_admin_queues.py``) is read-only; this file pins the write
side that hangs off it: a verdict on the object itself, a single complaint being
dismissed, a single PII flag being closed. Every action follows POST-redirect-GET
-- a success is a 303 back to the hub and never a re-render, so a refresh cannot
replay the decision -- and a refusal re-renders the *same* hub with a message
instead of a bare error page.

Two rules get their own checks: the ``next`` field is attacker-controlled (it is
just a form value), so it must never turn into an open redirect; and a decision
the service does not know must leave the object and the trail untouched.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.audit import (  # noqa: E402
    PII_FLAG_STATUS_DISMISSED,
    PII_FLAG_STATUS_RESOLVED,
    PiiFlag,
)
from app.models.content import (  # noqa: E402
    CONTENT_STATUS_DELETED,
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Post,
)
from app.models.report import (  # noqa: E402
    REPORT_STATUS_DISMISSED,
    REPORT_STATUS_OPEN,
    REPORT_STATUS_RESOLVED,
    Report,
)
from app.models.student import Student  # noqa: E402
from app.repositories import audit as audit_repo  # noqa: E402
from app.repositories import pii_flags as pii_flags_repo  # noqa: E402
from app.repositories import posts as posts_repo  # noqa: E402
from app.repositories import reports as reports_repo  # noqa: E402
from app.services.moderation import MAX_NOTE_LENGTH  # noqa: E402

_CSRF_FIELD_RE = re.compile(r'name="csrf_token" value="([^"]*)"')


def _make_student(session_factory, alias: str) -> int:
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        return student.id


def _make_post(
    session_factory, author_id: int, alias: str, *, body: str = "Текст поста"
) -> int:
    with session_factory() as db:
        post = Post(
            author_id=author_id,
            display_alias=alias,
            is_anonymous=False,
            body=body,
            status=CONTENT_STATUS_VISIBLE,
        )
        db.add(post)
        db.commit()
        return post.id


def _make_report(
    session_factory, reporter_id: int, target_type: str, target_id: int
) -> int:
    with session_factory() as db:
        report = reports_repo.create_report(
            db,
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            reason_code="spam",
            comment=None,
        )
        db.commit()
        return report.id


def _make_flag(session_factory, target_type: str, target_id: int) -> int:
    with session_factory() as db:
        flag = pii_flags_repo.create_flag(
            db,
            target_type=target_type,
            target_id=target_id,
            rule_code="phone",
            snippet="+7 ...",
        )
        db.commit()
        return flag.id


def _csrf_from(client, page: str) -> str:
    """Token from a public page, for tests that hold no session yet."""
    response = client.get(page)
    assert response.status_code == 200, response.text
    match = _CSRF_FIELD_RE.search(response.text)
    assert match, "the page rendered no csrf_token field"
    return match.group(1)


def _post_status(session_factory, post_id: int) -> str:
    with session_factory() as db:
        return posts_repo.get_post(db, post_id).status


def _report_status(session_factory, report_id: int) -> str:
    with session_factory() as db:
        return db.get(Report, report_id).status


def _flag_status(session_factory, flag_id: int) -> str:
    with session_factory() as db:
        return db.get(PiiFlag, flag_id).status


def _audit_count(session_factory) -> int:
    with session_factory() as db:
        return audit_repo.count_all(db)


def _hub(target_type: str, target_id: int) -> str:
    return f"/admin/content/{target_type}/{target_id}"


# --- The POST surface is protected like every other state change --------------


def test_post_without_a_csrf_token_is_refused(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    response = admin.client.post(
        f"{_hub('post', post_id)}/decide",
        data={"decision": "hide"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_VISIBLE


def test_post_without_an_admin_session_redirects_to_login(client, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    token = _csrf_from(client, "/admin/login")

    response = client.post(
        f"{_hub('post', post_id)}/decide",
        data={"decision": "hide", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_VISIBLE


# --- The object decision ------------------------------------------------------


def test_hide_closes_the_open_report_and_writes_the_trail(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    reporter = _make_student(session_factory, "Ученик-жалобщик")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    report_id = _make_report(session_factory, reporter, "post", post_id)

    response = admin.post(
        f"{_hub('post', post_id)}/decide",
        _hub("post", post_id),
        {"decision": "hide"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == _hub("post", post_id)
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_HIDDEN
    assert _report_status(session_factory, report_id) == REPORT_STATUS_RESOLVED
    # The hide and the closure of the complaint both land in the trail.
    assert _audit_count(session_factory) == 2


def test_delete_is_terminal_and_restore_is_refused_without_a_new_trail_row(
    admin, session_factory
):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    deleted = admin.post(
        f"{_hub('post', post_id)}/decide", _hub("post", post_id), {"decision": "delete"}
    )
    assert deleted.status_code == 303
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_DELETED
    trail_after_delete = _audit_count(session_factory)

    restore = admin.post(
        f"{_hub('post', post_id)}/decide",
        _hub("post", post_id),
        {"decision": "restore"},
    )

    # Refused: the same hub comes back, with the reason, as a 200 not a redirect.
    assert restore.status_code == 200
    assert "Удалённый объект вернуть нельзя" in restore.text
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_DELETED
    assert _audit_count(session_factory) == trail_after_delete


def test_ban_blocks_the_author_and_the_hub_drops_the_ban_option(
    admin, session_factory
):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    response = admin.post(
        f"{_hub('post', post_id)}/decide", _hub("post", post_id), {"decision": "ban"}
    )
    assert response.status_code == 303

    with session_factory() as db:
        assert db.get(Student, author).is_banned is True

    hub = admin.client.get(_hub("post", post_id))
    assert hub.status_code == 200
    assert 'value="ban"' not in hub.text
    assert "Автор уже заблокирован" in hub.text


def test_unknown_decision_is_refused_and_changes_nothing(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    response = admin.post(
        f"{_hub('post', post_id)}/decide", _hub("post", post_id), {"decision": "purge"}
    )

    assert response.status_code == 200
    assert "Неизвестное действие" in response.text
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_VISIBLE
    assert _audit_count(session_factory) == 0


def test_over_long_note_is_refused_and_changes_nothing(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    response = admin.post(
        f"{_hub('post', post_id)}/decide",
        _hub("post", post_id),
        {"decision": "hide", "note": "x" * (MAX_NOTE_LENGTH + 1)},
    )

    assert response.status_code == 200
    assert "Заметка слишком длинная" in response.text
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_VISIBLE
    assert _audit_count(session_factory) == 0


# --- `next` cannot leave the admin area ---------------------------------------


def test_next_to_a_foreign_site_is_ignored(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    for hostile in ("https://evil.example", "//evil.example"):
        response = admin.post(
            f"{_hub('post', post_id)}/decide",
            _hub("post", post_id),
            {"decision": "hide", "next": hostile},
        )
        assert response.status_code == 303
        assert response.headers["location"] == _hub("post", post_id)


def test_a_safe_next_is_honoured(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    response = admin.post(
        f"{_hub('post', post_id)}/decide",
        _hub("post", post_id),
        {"decision": "hide", "next": "/admin/reports"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/reports"


# --- Complaints one at a time -------------------------------------------------


def test_dismissing_one_complaint_leaves_the_other_open(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    reporter_one = _make_student(session_factory, "Ученик-жалобщик-1")
    reporter_two = _make_student(session_factory, "Ученик-жалобщик-2")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    report_one = _make_report(session_factory, reporter_one, "post", post_id)
    report_two = _make_report(session_factory, reporter_two, "post", post_id)

    response = admin.post(
        f"/admin/reports/{report_one}/dismiss",
        _hub("post", post_id),
        {},
    )

    assert response.status_code == 303
    assert response.headers["location"] == _hub("post", post_id)
    assert _report_status(session_factory, report_one) == REPORT_STATUS_DISMISSED
    assert _report_status(session_factory, report_two) == REPORT_STATUS_OPEN


def test_dismissing_a_missing_complaint_answers_404(admin):
    # The login page is just a handy form that carries a matching CSRF token.
    response = admin.post("/admin/reports/999/dismiss", "/admin/login", {})

    assert response.status_code == 404


# --- PII flags ----------------------------------------------------------------


def test_flag_resolve_and_dismiss_set_the_two_closed_states(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    flag_accepted = _make_flag(session_factory, "post", post_id)
    flag_rejected = _make_flag(session_factory, "post", post_id)

    accepted = admin.post(
        f"/admin/pii-flags/{flag_accepted}/resolve", _hub("post", post_id), {}
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == _hub("post", post_id)

    rejected = admin.post(
        f"/admin/pii-flags/{flag_rejected}/dismiss", _hub("post", post_id), {}
    )
    assert rejected.status_code == 303

    assert _flag_status(session_factory, flag_accepted) == PII_FLAG_STATUS_RESOLVED
    assert _flag_status(session_factory, flag_rejected) == PII_FLAG_STATUS_DISMISSED


def test_closing_a_missing_flag_answers_404(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    response = admin.post(
        "/admin/pii-flags/999/resolve", _hub("post", post_id), {}
    )

    assert response.status_code == 404


def test_a_flag_still_open_is_listed_on_the_hub(admin, session_factory):
    """The flag forms exist only while the flag is open -- a closed one is gone."""
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    flag_id = _make_flag(session_factory, "post", post_id)

    before = admin.client.get(_hub("post", post_id))
    assert f'action="/admin/pii-flags/{flag_id}/resolve"' in before.text

    admin.post(f"/admin/pii-flags/{flag_id}/dismiss", _hub("post", post_id), {})

    with session_factory() as db:
        assert db.get(PiiFlag, flag_id).status == PII_FLAG_STATUS_DISMISSED
    after = admin.client.get(_hub("post", post_id))
    assert f'action="/admin/pii-flags/{flag_id}/resolve"' not in after.text
