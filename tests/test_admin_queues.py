"""The moderation queue screens (``app.routers.admin_moderation``).

The reports queue is the first of the moderation screens; stage 13 grows this
file as more queues land. These tests pin what the queue has to show (who
complained, about what, why and how many complaints the object carries), that
it pages like the student listings, and that it never hides the fact a target
was already dealt with -- the reader behind it has no visibility filter.

The object hub (``/admin/content/{type}/{id}``) is the second screen: the tests
below pin the same contrast one level down -- the full object, its open
complaints and flags and its trail render for a moderator even when the object
is hidden from students.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.audit import (  # noqa: E402
    ACTION_BAN,
    ACTION_HIDE,
    ACTION_RESOLVE_REPORT,
)
from app.models.content import (  # noqa: E402
    CONTENT_STATUS_DELETED,
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Post,
)
from app.models.student import Student  # noqa: E402
from app.repositories import admins as admins_repo  # noqa: E402
from app.repositories import audit as audit_repo  # noqa: E402
from app.repositories import pii_blocked as pii_blocked_repo  # noqa: E402
from app.repositories import pii_flags as pii_flags_repo  # noqa: E402
from app.repositories import reports as reports_repo  # noqa: E402
from app.repositories import students as students_repo  # noqa: E402
from app.services import moderation as moderation_service  # noqa: E402


def _make_student(session_factory, alias: str) -> int:
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        return student.id


def _make_post(
    session_factory,
    author_id: int,
    alias: str,
    *,
    body: str = "Текст поста",
    status: str = CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        post = Post(
            author_id=author_id,
            display_alias=alias,
            is_anonymous=False,
            body=body,
            status=status,
        )
        db.add(post)
        db.commit()
        return post.id


def _make_report(
    session_factory,
    reporter_id: int,
    target_type: str,
    target_id: int,
    *,
    reason_code: str = "spam",
    comment: str | None = None,
) -> int:
    with session_factory() as db:
        report = reports_repo.create_report(
            db,
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            comment=comment,
        )
        db.commit()
        return report.id


def _make_flag(
    session_factory, target_type: str, target_id: int, *, rule_code: str = "phone"
) -> int:
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


def _make_blocked(
    session_factory, student_id: int, target_type: str, *rule_codes: str
) -> int:
    with session_factory() as db:
        attempt = pii_blocked_repo.record_attempt(
            db,
            student_id=student_id,
            target_type=target_type,
            rule_codes=rule_codes,
        )
        db.commit()
        return attempt.id


def _row_count(html: str) -> int:
    """Number of queue rows, each carrying exactly one link into its hub."""
    return html.count('href="/admin/content/')


def test_anonymous_is_redirected_but_an_admin_opens_the_queue(client, admin):
    anonymous = client.get("/admin/reports", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login"

    assert admin.client.get("/admin/reports").status_code == 200


def test_empty_queue_shows_the_empty_state(admin):
    response = admin.client.get("/admin/reports")

    assert response.status_code == 200
    assert "Открытых жалоб нет" in response.text


def test_open_report_is_listed_with_reason_target_and_hub_link(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    reporter = _make_student(session_factory, "Ученик-жалобщик")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    _make_report(session_factory, reporter, "post", post_id, reason_code="spam")

    response = admin.client.get("/admin/reports")

    assert response.status_code == 200
    assert "Спам или реклама" in response.text
    # The reporter and the target's author are both moderation information, so
    # both aliases are shown (a student never sees the reporter's).
    assert "Ученик-жалобщик" in response.text
    assert "Ученик-автор" in response.text
    assert ">Пост<" in response.text
    assert f'href="/admin/content/post/{post_id}"' in response.text


def test_queue_paginates_and_coerces_a_bad_page(admin, session_factory):
    author = _make_student(session_factory, "Автор")
    post_id = _make_post(session_factory, author, "Автор")
    # One complaint per student (the unique constraint), all on the same object.
    with session_factory() as db:
        for index in range(21):
            reporter = Student(alias=f"Жалобщик-{index:02d}")
            db.add(reporter)
            db.flush()
            reports_repo.create_report(
                db,
                reporter_id=reporter.id,
                target_type="post",
                target_id=post_id,
                reason_code="spam",
                comment=None,
            )
        db.commit()

    first = admin.client.get("/admin/reports")
    assert first.status_code == 200
    assert _row_count(first.text) == 20
    assert 'href="/admin/reports?page=2"' in first.text
    # More than one complaint on the object, so its counter is shown.
    assert "Жалоб на объект: 21" in first.text

    second = admin.client.get("/admin/reports?page=2")
    assert _row_count(second.text) == 1

    junk = admin.client.get("/admin/reports?page=abc")
    assert _row_count(junk.text) == 20
    assert "Страница 1 из 2" in junk.text

    beyond = admin.client.get("/admin/reports?page=99")
    assert _row_count(beyond.text) == 1
    assert "Страница 2 из 2" in beyond.text


def test_hidden_target_shows_that_it_was_already_dealt_with(admin, session_factory):
    author = _make_student(session_factory, "Автор")
    reporter = _make_student(session_factory, "Жалобщик")
    post_id = _make_post(
        session_factory, author, "Автор", status=CONTENT_STATUS_HIDDEN
    )
    _make_report(session_factory, reporter, "post", post_id, reason_code="abuse")

    response = admin.client.get("/admin/reports")

    assert response.status_code == 200
    assert "Скрыто" in response.text
    assert f'href="/admin/content/post/{post_id}"' in response.text


def test_a_report_whose_object_is_gone_offers_no_link(admin, session_factory):
    """A row that does not resolve says so and links nowhere, not to a 404.

    Nothing hard-deletes a post today, so this needs a report written by hand --
    but the queue renders such a row either way, and the link would be a dead
    end. The flag queue has the same branch and the same test below.
    """
    reporter = _make_student(session_factory, "Жалобщик на призрак")
    _make_report(session_factory, reporter, "post", 9999)

    response = admin.client.get("/admin/reports")

    assert response.status_code == 200
    assert "Объект не найден" in response.text
    assert 'href="/admin/content/post/9999"' not in response.text


# --- The object hub (``/admin/content/{type}/{id}``) -------------------------


def test_anonymous_is_redirected_but_an_admin_opens_the_hub(
    client, admin, session_factory
):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    anonymous = client.get(
        f"/admin/content/post/{post_id}", follow_redirects=False
    )
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login"

    assert admin.client.get(f"/admin/content/post/{post_id}").status_code == 200


def test_unknown_type_and_missing_id_answer_404(admin):
    assert admin.client.get("/admin/content/foobar/1").status_code == 404
    assert admin.client.get("/admin/content/post/999").status_code == 404


def test_hidden_object_is_visible_on_the_hub_but_not_to_students(
    admin, student, session_factory
):
    """The contrast the hub exists for: a moderator reads what a student cannot.

    The same hidden post renders in full on the hub, while the feed 404s it for
    a signed-in student -- moderation's reader has no visibility filter.
    """
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(
        session_factory,
        author,
        "Ученик-автор",
        body="Скрытый текст",
        status=CONTENT_STATUS_HIDDEN,
    )

    hub = admin.client.get(f"/admin/content/post/{post_id}")
    assert hub.status_code == 200
    assert "Скрытый текст" in hub.text
    assert "Скрыто" in hub.text

    public = student.client.get(f"/feed/posts/{post_id}")
    assert public.status_code == 404


def test_hub_lists_the_open_report_flag_trail_and_student_link(
    admin, session_factory
):
    author = _make_student(session_factory, "Ученик-автор")
    reporter = _make_student(session_factory, "Ученик-жалобщик")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    _make_report(session_factory, reporter, "post", post_id, reason_code="spam")
    _make_flag(session_factory, "post", post_id, rule_code="phone")
    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin.admin_id,
            action=ACTION_HIDE,
            target_type="post",
            target_id=post_id,
            note="Травля",
        )
        db.commit()

    response = admin.client.get(f"/admin/content/post/{post_id}")

    assert response.status_code == 200
    assert "Спам или реклама" in response.text
    assert "Ученик-жалобщик" in response.text
    # The PII flag is shown by its rule's human name, not the raw code.
    assert "Телефон" in response.text
    assert f'href="/admin/students/{author}"' in response.text
    # The trail names the action, its admin and the note it carried.
    assert "Скрыто" in response.text
    assert admin.username in response.text
    assert "Травля" in response.text


def test_hub_shows_the_russian_action_label_after_a_decision(
    admin, session_factory
):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    with session_factory() as db:
        admin_row = admins_repo.get_admin_by_id(db, admin.admin_id)
        moderation_service.apply_content_action(
            db,
            admin_row,
            target_type="post",
            target_id=post_id,
            action=ACTION_HIDE,
        )

    response = admin.client.get(f"/admin/content/post/{post_id}")

    assert response.status_code == 200
    assert "Что уже делали" in response.text
    assert "Скрыто" in response.text
    # The trail renders the Russian name, never the raw code.
    assert "hide" not in response.text


def test_the_decision_form_offers_only_what_the_status_allows(admin, session_factory):
    """Which decisions a hub offers is decided per status.

    The template picks the options by comparing the target's status, so this is
    where the three branches are pinned: a wrong comparison (or a status nobody
    thought about) would silently offer a decision the service must then refuse,
    or hide one the moderator is entitled to.
    """
    author = _make_student(session_factory, "Ученик-автор")
    visible = _make_post(session_factory, author, "Ученик-автор")
    hidden = _make_post(
        session_factory, author, "Ученик-автор", status=CONTENT_STATUS_HIDDEN
    )
    deleted = _make_post(
        session_factory, author, "Ученик-автор", status=CONTENT_STATUS_DELETED
    )

    def decide_options(post_id: int) -> str:
        response = admin.client.get(f"/admin/content/post/{post_id}")
        assert response.status_code == 200
        start = response.text.index('name="decision"')
        return response.text[start:].split("</fieldset>")[0]

    visible_form = decide_options(visible)
    assert 'value="hide"' in visible_form
    assert 'value="delete"' in visible_form
    assert 'value="ban"' in visible_form
    assert 'value="restore"' not in visible_form

    hidden_form = decide_options(hidden)
    assert 'value="restore"' in hidden_form
    assert 'value="hide"' not in hidden_form
    assert 'value="delete"' in hidden_form

    deleted_form = decide_options(deleted)
    assert 'value="restore"' not in deleted_form
    assert 'value="hide"' not in deleted_form
    assert 'value="ban"' in deleted_form
    assert "Удалённый объект вернуть нельзя" in deleted_form


def test_a_banned_author_is_not_offered_for_banning_again(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    with session_factory() as db:
        students_repo.set_banned(
            db, students_repo.get_student(db, author), banned=True, reason="травля"
        )
        db.commit()

    response = admin.client.get(f"/admin/content/post/{post_id}")

    assert response.status_code == 200
    assert 'value="ban"' not in response.text
    assert "Автор уже заблокирован" in response.text


# --- The PII flag queue (``/admin/pii``) -------------------------------------


def test_anonymous_is_redirected_but_an_admin_opens_the_flag_queue(client, admin):
    anonymous = client.get("/admin/pii", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login"

    assert admin.client.get("/admin/pii").status_code == 200


def test_empty_flag_queue_shows_the_empty_state(admin):
    response = admin.client.get("/admin/pii")

    assert response.status_code == 200
    assert "Открытых флагов ПДн нет" in response.text


def test_open_flag_is_listed_with_rule_snippet_and_hub_link(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    _make_flag(session_factory, "post", post_id, rule_code="phone")

    response = admin.client.get("/admin/pii")

    assert response.status_code == 200
    # The rule is named in Russian, the snippet is the filter's truncated fragment.
    assert "Телефон" in response.text
    assert "+7 ..." in response.text
    assert ">Пост<" in response.text
    assert f'href="/admin/content/post/{post_id}"' in response.text


def test_a_flag_whose_object_is_gone_offers_no_link(admin, session_factory):
    """The same rule as the reports queue: an unresolvable row links nowhere."""
    _make_flag(session_factory, "post", 9999, rule_code="phone")

    response = admin.client.get("/admin/pii")

    assert response.status_code == 200
    assert "Объект не найден" in response.text
    assert 'href="/admin/content/post/9999"' not in response.text


def test_accepting_a_flag_from_the_queue_returns_to_the_queue(
    admin, session_factory
):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    flag_id = _make_flag(session_factory, "post", post_id, rule_code="phone")

    page = admin.client.get("/admin/pii")
    assert page.status_code == 200
    # The queue's own forms send the moderator back to the queue, not to the hub.
    assert 'value="/admin/pii?page=1"' in page.text

    response = admin.post(
        f"/admin/pii-flags/{flag_id}/resolve",
        "/admin/pii",
        {"next": "/admin/pii?page=1"},
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/pii")

    after = admin.client.get("/admin/pii")
    assert "Открытых флагов ПДн нет" in after.text


def test_flag_queue_paginates(admin, session_factory):
    author = _make_student(session_factory, "Автор")
    post_id = _make_post(session_factory, author, "Автор")
    with session_factory() as db:
        for _ in range(21):
            pii_flags_repo.create_flag(
                db,
                target_type="post",
                target_id=post_id,
                rule_code="phone",
                snippet="+7 ...",
            )
        db.commit()

    first = admin.client.get("/admin/pii")
    assert first.status_code == 200
    assert _row_count(first.text) == 20
    assert 'href="/admin/pii?page=2"' in first.text
    # More than one flag on the object, so its counter is shown.
    assert "Флагов на объект: 21" in first.text

    second = admin.client.get("/admin/pii?page=2")
    assert _row_count(second.text) == 1


# --- The refusal journal (``/admin/blocked``) --------------------------------


def test_anonymous_is_redirected_but_an_admin_opens_the_blocked_journal(client, admin):
    anonymous = client.get("/admin/blocked", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login"

    assert admin.client.get("/admin/blocked").status_code == 200


def test_empty_blocked_journal_shows_the_empty_state(admin):
    response = admin.client.get("/admin/blocked")

    assert response.status_code == 200
    assert "Отказов не было" in response.text


def test_blocked_entry_names_the_student_target_and_rules(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    _make_blocked(session_factory, author, "post", "phone")
    _make_blocked(session_factory, author, "comment", "email")

    response = admin.client.get("/admin/blocked")

    assert response.status_code == 200
    assert "Ученик-автор" in response.text
    assert ">Пост<" in response.text
    assert ">Комментарий<" in response.text
    # Rule codes are shown as their Russian names, never raw.
    assert "Телефон" in response.text
    assert "E-mail" in response.text


def test_blocked_entry_carries_no_text_and_no_hub_link(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    _make_blocked(session_factory, author, "post", "phone")

    response = admin.client.get("/admin/blocked")

    # A refusal never had an object, so there is nothing to open in the hub.
    assert "/admin/content/" not in response.text
    # The hard filter stores no fragment of the refused message.
    assert "+7 ..." not in response.text


def test_blocked_entry_shows_an_unknown_rule_code_as_is(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    _make_blocked(session_factory, author, "post", "brand_new_rule")

    response = admin.client.get("/admin/blocked")

    assert "brand_new_rule" in response.text


def test_blocked_journal_paginates(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    with session_factory() as db:
        for _ in range(21):
            pii_blocked_repo.record_attempt(
                db, student_id=author, target_type="post", rule_codes=("phone",)
            )
        db.commit()

    first = admin.client.get("/admin/blocked")
    assert first.status_code == 200
    assert first.text.count('class="post-card"') == 20
    assert 'href="/admin/blocked?page=2"' in first.text

    second = admin.client.get("/admin/blocked?page=2")
    assert second.text.count('class="post-card"') == 1


# --- The student card (``/admin/students/{id}``) -----------------------------


def test_anonymous_is_redirected_but_an_admin_opens_the_student_card(
    client, admin, session_factory
):
    student_id = _make_student(session_factory, "Ученик-01")

    anonymous = client.get(f"/admin/students/{student_id}", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login"

    assert admin.client.get(f"/admin/students/{student_id}").status_code == 200


def test_a_missing_student_answers_404(admin):
    assert admin.client.get("/admin/students/999").status_code == 404


def test_banning_from_the_card_sets_the_reason_and_flips_the_form(
    admin, session_factory
):
    student_id = _make_student(session_factory, "Ученик-01")

    response = admin.post(
        f"/admin/students/{student_id}/ban",
        f"/admin/students/{student_id}",
        {"note": "травля в комментариях"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/students/{student_id}"

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is True
        assert student.banned_reason == "травля в комментариях"

    card = admin.client.get(f"/admin/students/{student_id}")
    assert card.status_code == 200
    assert "Автор заблокирован" in card.text
    assert "травля в комментариях" in card.text
    # Once the ban stands the ban form is gone and the unban form is there.
    assert f'action="/admin/students/{student_id}/ban"' not in card.text
    assert f'action="/admin/students/{student_id}/unban"' in card.text


def test_unbanning_clears_the_reason_and_records_the_action(admin, session_factory):
    student_id = _make_student(session_factory, "Ученик-01")
    admin.post(
        f"/admin/students/{student_id}/ban",
        f"/admin/students/{student_id}",
        {"note": "травля"},
    )

    response = admin.post(
        f"/admin/students/{student_id}/unban",
        f"/admin/students/{student_id}",
        {"note": "разобрались"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/students/{student_id}"

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is False
        assert student.banned_reason is None

    card = admin.client.get(f"/admin/students/{student_id}")
    assert "Автор разблокирован" in card.text
    assert f'action="/admin/students/{student_id}/ban"' in card.text
    assert f'action="/admin/students/{student_id}/unban"' not in card.text


def test_a_repeated_ban_writes_no_second_trail_row(admin, session_factory):
    student_id = _make_student(session_factory, "Ученик-01")
    admin.post(
        f"/admin/students/{student_id}/ban",
        f"/admin/students/{student_id}",
        {"note": "травля"},
    )
    with session_factory() as db:
        before = audit_repo.count_all(db)

    again = admin.post(
        f"/admin/students/{student_id}/ban",
        f"/admin/students/{student_id}",
        {"note": "ещё раз"},
    )
    assert again.status_code == 303
    with session_factory() as db:
        assert audit_repo.count_all(db) == before


def test_an_over_long_note_is_refused_and_leaves_the_student_free(
    admin, session_factory
):
    student_id = _make_student(session_factory, "Ученик-01")

    response = admin.post(
        f"/admin/students/{student_id}/ban",
        f"/admin/students/{student_id}",
        {"note": "x" * (moderation_service.MAX_NOTE_LENGTH + 1)},
    )
    assert response.status_code == 200
    assert "Заметка слишком длинная" in response.text

    with session_factory() as db:
        student = students_repo.get_student(db, student_id)
        assert student.is_banned is False


def test_a_foreign_next_is_ignored_and_the_card_is_used(admin, session_factory):
    student_id = _make_student(session_factory, "Ученик-01")

    response = admin.post(
        f"/admin/students/{student_id}/ban",
        f"/admin/students/{student_id}",
        {"next": "https://evil.example"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/students/{student_id}"
    assert "evil.example" not in response.headers["location"]


def test_the_card_links_to_content_only_when_the_trail_touches_some(
    admin, session_factory
):
    student_id = _make_student(session_factory, "Ученик-01")

    # Nothing but a ban on the student themselves: there is no object to open.
    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin.admin_id,
            action=ACTION_BAN,
            target_type="student",
            target_id=student_id,
            note="травля",
        )
        db.commit()

    card = admin.client.get(f"/admin/students/{student_id}")
    assert card.status_code == 200
    assert "/admin/content/" not in card.text

    # An action on the student's own post brings a link to that post's hub.
    post_id = _make_post(session_factory, student_id, "Ученик-01")
    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin.admin_id,
            action=ACTION_HIDE,
            target_type="post",
            target_id=post_id,
            note=None,
        )
        db.commit()

    card = admin.client.get(f"/admin/students/{student_id}")
    assert f'href="/admin/content/post/{post_id}"' in card.text


# --- The audit journal (``/admin/audit``) ------------------------------------


def test_anonymous_is_redirected_but_an_admin_opens_the_audit_journal(client, admin):
    anonymous = client.get("/admin/audit", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/admin/login"

    assert admin.client.get("/admin/audit").status_code == 200


def test_empty_audit_journal_shows_the_empty_state(admin):
    response = admin.client.get("/admin/audit")

    assert response.status_code == 200
    assert "Действий пока не было" in response.text


def test_audit_row_names_the_action_target_admin_and_note(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    with session_factory() as db:
        admin_row = admins_repo.get_admin_by_id(db, admin.admin_id)
        moderation_service.apply_content_action(
            db,
            admin_row,
            target_type="post",
            target_id=post_id,
            action=ACTION_HIDE,
            note="Травля",
        )

    response = admin.client.get("/admin/audit")

    assert response.status_code == 200
    assert "Скрыто" in response.text
    assert "Пост" in response.text
    assert admin.username in response.text
    assert "Травля" in response.text
    # The journal renders the Russian name, never the raw code.
    assert "hide" not in response.text


def test_the_journal_links_content_and_students(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin.admin_id,
            action=ACTION_HIDE,
            target_type="post",
            target_id=post_id,
        )
        db.commit()

    content = admin.client.get("/admin/audit")
    assert content.status_code == 200
    assert f'href="/admin/content/post/{post_id}"' in content.text

    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin.admin_id,
            action=ACTION_BAN,
            target_type="student",
            target_id=author,
        )
        db.commit()

    students = admin.client.get("/admin/audit")
    assert f'href="/admin/students/{author}"' in students.text


def test_a_report_entry_gets_no_hub_link(admin, session_factory):
    with session_factory() as db:
        audit_repo.record_action(
            db,
            admin_id=admin.admin_id,
            action=ACTION_RESOLVE_REPORT,
            target_type="report",
            target_id=1,
        )
        db.commit()

    response = admin.client.get("/admin/audit")

    assert response.status_code == 200
    assert "Жалоба" in response.text
    # A report entry points at a complaint, which has no public object to open.
    assert "/admin/content/" not in response.text
    assert "/admin/students/" not in response.text


def test_audit_journal_paginates(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")
    with session_factory() as db:
        for _ in range(21):
            audit_repo.record_action(
                db,
                admin_id=admin.admin_id,
                action=ACTION_HIDE,
                target_type="post",
                target_id=post_id,
            )
        db.commit()

    first = admin.client.get("/admin/audit")
    assert first.status_code == 200
    assert first.text.count('class="post-card"') == 20
    assert 'href="/admin/audit?page=2"' in first.text

    second = admin.client.get("/admin/audit?page=2")
    assert second.text.count('class="post-card"') == 1
