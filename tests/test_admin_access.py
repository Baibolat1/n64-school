"""Who may open the admin pages at all.

The admin area hangs off its own cookie (``settings.SESSION_COOKIE_NAME``)
resolved server-side, so it is deliberately independent from the student
session: holding one must never be a way into the other. These are the cases
that hold for every admin page, whichever screen it is.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.content import Post  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.repositories import pii_blocked as pii_blocked_repo  # noqa: E402
from app.repositories import pii_flags as pii_flags_repo  # noqa: E402
from app.repositories import reports as reports_repo  # noqa: E402
from app.repositories import students as students_repo  # noqa: E402
from app.repositories.admins import create_admin  # noqa: E402
from app.services import invites as invites_service  # noqa: E402
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME  # noqa: E402

# A protected page that exists today, so the access rules can be checked before
# the moderation screens land.
PROTECTED_PAGE = "/admin/invites"

DASHBOARD = "/admin"

# The hidden field every form renders; matching it is how a test gets a token
# that goes with the CSRF cookie the same response set.
_CSRF_FIELD_RE = re.compile(r'name="csrf_token" value="([^"]*)"')


def _counter(html: str, label: str) -> int:
    """The number rendered on the dashboard card carrying ``label``.

    The template lays each card out as the value followed by its label, so the
    pair anchors the number to the queue it counts.
    """
    pattern = (
        r'<span class="stat-value">(\d+)</span>\s*'
        r'<span class="stat-label">' + re.escape(label) + r"</span>"
    )
    match = re.search(pattern, html)
    assert match, f"no counter labelled {label!r} on the dashboard"
    return int(match.group(1))


def test_anonymous_visitor_is_redirected_to_the_login_page(client):
    response = client.get(PROTECTED_PAGE, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_session_opens_the_page(admin):
    response = admin.client.get(PROTECTED_PAGE)

    # 200 from a ``require_admin`` route is the proof: an unresolved session
    # would have become the 303 the test above pins down.
    assert response.status_code == 200


def test_student_session_does_not_open_the_admin_area(student):
    # A student cookie is a different cookie entirely: to the admin area they
    # are an anonymous visitor, not a lesser admin.
    response = student.client.get(PROTECTED_PAGE, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_and_student_sessions_coexist_in_one_test(admin, student):
    # The two clients keep separate cookie jars, which is the whole reason the
    # admin fixture builds its own client.
    assert admin.client.get(PROTECTED_PAGE).status_code == 200
    assert student.client.get("/feed", follow_redirects=False).status_code == 200
    assert settings.SESSION_COOKIE_NAME in admin.client.cookies
    assert settings.STUDENT_COOKIE_NAME in student.client.cookies


def test_admin_post_helper_carries_a_valid_token(admin, session_factory):
    response = admin.post(PROTECTED_PAGE, PROTECTED_PAGE, {"ttl_days": 3})

    assert response.status_code == 200

    with session_factory() as db:
        assert len(invites_service.list_invites(db)) == 1


# The dashboard overview: one counter per moderation queue, each linking into
# the queue it counts.


def test_dashboard_is_empty_before_anything_is_queued(admin):
    response = admin.client.get(DASHBOARD)

    assert response.status_code == 200
    assert _counter(response.text, "Открытые жалобы") == 0
    assert _counter(response.text, "Открытые флаги ПДн") == 0
    assert _counter(response.text, "Отказы публикации (ПДн)") == 0
    assert _counter(response.text, "Заблокированные ученики") == 0


def test_dashboard_links_into_the_queues(admin):
    response = admin.client.get(DASHBOARD)

    assert response.status_code == 200
    assert 'href="/admin/reports"' in response.text
    assert 'href="/admin/pii"' in response.text
    assert 'href="/admin/blocked"' in response.text


def test_dashboard_counters_reflect_the_queues(admin, session_factory):
    with session_factory() as db:
        reporter = Student(alias="Ученик-жалобщик")
        target = Student(alias="Ученик-нарушитель")
        db.add_all([reporter, target])
        db.flush()
        reports_repo.create_report(
            db,
            reporter_id=reporter.id,
            target_type="post",
            target_id=1,
            reason_code="spam",
            comment=None,
        )
        pii_flags_repo.create_flag(
            db,
            target_type="post",
            target_id=1,
            rule_code="phone",
            snippet="+7 ...",
        )
        pii_blocked_repo.record_attempt(
            db, student_id=target.id, target_type="post", rule_codes=("phone",)
        )
        students_repo.set_banned(db, target, banned=True, reason="спам")
        db.commit()

    response = admin.client.get(DASHBOARD)

    assert response.status_code == 200
    assert _counter(response.text, "Открытые жалобы") == 1
    assert _counter(response.text, "Открытые флаги ПДн") == 1
    assert _counter(response.text, "Отказы публикации (ПДн)") == 1
    assert _counter(response.text, "Заблокированные ученики") == 1


# The section navigation: one menu on every moderation screen, absent from the
# login page and from the student area.

# The six screens the section menu links to, in the order the partial renders.
ADMIN_NAV_LINKS = (
    "/admin",
    "/admin/reports",
    "/admin/pii",
    "/admin/blocked",
    "/admin/audit",
    "/admin/invites",
)


def _assert_full_nav(html: str) -> None:
    """The section menu is present and links to every screen of the section."""
    assert '<nav class="admin-nav"' in html
    for href in ADMIN_NAV_LINKS:
        assert f'href="{href}"' in html, f"the nav is missing {href}"


def _assert_active(html: str, href: str) -> None:
    """Exactly ``href`` is the active item of the section menu."""
    assert f'<a href="{href}" class="is-active">' in html
    for other in ADMIN_NAV_LINKS:
        if other != href:
            assert f'<a href="{other}" class="is-active">' not in html


def _make_student(session_factory, alias: str) -> int:
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        return student.id


def _make_post(session_factory, author_id: int, alias: str) -> int:
    with session_factory() as db:
        post = Post(author_id=author_id, display_alias=alias, body="Текст поста")
        db.add(post)
        db.commit()
        return post.id


def test_the_navigation_is_on_every_admin_screen(admin, session_factory):
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    pages = (
        "/admin",
        "/admin/invites",
        "/admin/reports",
        "/admin/pii",
        "/admin/blocked",
        "/admin/audit",
        f"/admin/content/post/{post_id}",
        f"/admin/students/{author}",
    )
    for page in pages:
        response = admin.client.get(page)
        assert response.status_code == 200, page
        _assert_full_nav(response.text)


def test_the_created_code_screen_carries_the_navigation(admin):
    # invite_created.html is only reachable through the POST that renders it.
    response = admin.post("/admin/invites", "/admin/invites", {"ttl_days": 3})

    assert response.status_code == 200
    _assert_full_nav(response.text)


def test_the_active_item_tracks_the_screen(admin):
    # The query string is not part of the path, so a paged queue stays active.
    for page, href in (
        ("/admin", "/admin"),
        ("/admin/reports", "/admin/reports"),
        ("/admin/reports?page=2", "/admin/reports"),
        ("/admin/pii", "/admin/pii"),
        ("/admin/blocked", "/admin/blocked"),
        ("/admin/audit", "/admin/audit"),
        ("/admin/invites", "/admin/invites"),
    ):
        response = admin.client.get(page)
        assert response.status_code == 200, page
        _assert_active(response.text, href)


def test_a_nested_screen_highlights_no_queue(admin, session_factory):
    # The object hub and the student card belong to no queue, so none lights up.
    author = _make_student(session_factory, "Ученик-автор")
    post_id = _make_post(session_factory, author, "Ученик-автор")

    for page in (f"/admin/content/post/{post_id}", f"/admin/students/{author}"):
        response = admin.client.get(page)
        assert response.status_code == 200, page
        for href in ADMIN_NAV_LINKS:
            assert f'<a href="{href}" class="is-active">' not in response.text, page


def test_the_login_page_has_no_section_navigation(client):
    response = client.get("/admin/login")

    assert response.status_code == 200
    assert 'class="admin-nav"' not in response.text
    for href in ADMIN_NAV_LINKS:
        assert f'href="{href}"' not in response.text


def test_a_student_page_has_no_admin_navigation(student):
    response = student.client.get("/feed")

    assert response.status_code == 200
    assert 'class="admin-nav"' not in response.text
    for href in ADMIN_NAV_LINKS:
        assert f'href="{href}"' not in response.text


# The admin session cookie carries the tightest SameSite: the whole admin area
# is same-site, so refusing to send it on an "outside in" navigation costs the
# moderator nothing. The other cookies keep the lax default: the CSRF cookie is
# checked right below, the student session cookie in tests/test_registration.py
# and tests/test_csrf.py.


def _csrf_from(client, page: str) -> str:
    """Token from a public page, for tests that hold no session yet."""
    response = client.get(page)
    assert response.status_code == 200, response.text
    match = _CSRF_FIELD_RE.search(response.text)
    assert match, "the page rendered no csrf_token field"
    return match.group(1)


def _set_cookie(response, name: str) -> str:
    """The ``Set-Cookie`` header for ``name`` (a response may carry several)."""
    prefix = f"{name}="
    headers = response.headers.get_list("set-cookie")
    for header in headers:
        if header.lower().startswith(prefix.lower()):
            return header
    raise AssertionError(f"no Set-Cookie for {name!r}: {headers}")


def test_admin_login_sets_a_strict_session_cookie(client, session_factory):
    # The login POST is the only place the real cookie is issued, so the header
    # is checked on the wire rather than on the fixture's hand-built session.
    with session_factory() as db:
        create_admin(
            db, username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD)
        )
        db.commit()
    token = _csrf_from(client, "/admin/login")

    response = client.post(
        "/admin/login",
        data={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    cookie = _set_cookie(response, settings.SESSION_COOKIE_NAME)
    assert "samesite=strict" in cookie.lower()


def test_the_other_cookies_stay_lax(client):
    # The CSRF cookie the login page issues is untouched by the admin change.
    response = client.get("/admin/login")

    assert "samesite=lax" in _set_cookie(response, settings.CSRF_COOKIE_NAME).lower()
