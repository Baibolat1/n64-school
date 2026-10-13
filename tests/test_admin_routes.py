"""The gates that hold for every admin route, checked route by route.

The screen-by-screen tests (``test_admin_access.py``, ``test_admin_queues.py``,
``test_admin_actions.py``) prove that each page does the right thing. This file
proves that none was forgotten: the table below is compared against the routes
the two admin routers actually serve, so a screen added without
``require_admin`` or ``require_csrf`` fails here instead of shipping.

The same table then carries four checks, one per rule:

* a GET from an anonymous visitor is a 303 to the login page (the login page is
  the single exception, and says so with ``public``);
* a POST with no CSRF token is a 403 -- the token is checked before the session,
  so this holds with or without one;
* a POST with a token but no session is a 303 to the login page;
* an admin's POST succeeds with the status the route promises (a 303, or 200
  where the answer is shown in place) and writes exactly one row to the trail,
  and repeating it writes none.

That last pair is what turns "one action, one row" from a promise in a docstring
into a property of the router.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models.audit import (  # noqa: E402
    ACTION_HIDE,
    PII_FLAG_STATUS_RESOLVED,
    TARGET_POST,
    PiiFlag,
)
from app.models.content import (  # noqa: E402
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Post,
)
from app.models.student import Student  # noqa: E402
from app.repositories import audit as audit_repo  # noqa: E402
from app.repositories import pii_flags as pii_flags_repo  # noqa: E402
from app.repositories import reports as reports_repo  # noqa: E402
from app.routers import admin as admin_router  # noqa: E402
from app.routers import admin_moderation as moderation_router  # noqa: E402
from tests.conftest import (  # noqa: E402
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    extract_csrf,
)

# A password that cannot be the real one, so the "no session" sweep of the login
# route fails to sign in for the right reason.
WRONG_PASSWORD = "заведомо-неверный-пароль"

_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


@dataclass(frozen=True)
class Case:
    """One admin route, with everything the sweeps need in order to call it.

    ``pattern`` is the route as registered (``/admin/students/{student_id}/ban``)
    and is what the coverage test compares against; ``path`` is the same route
    with a real id substituted -- ``{name}`` placeholders are filled from the
    ``subjects`` fixture -- and is what gets requested. The two differ only in
    the path parameters, and both are normalised before comparison, so the names
    are free to be descriptive.

    The expectations live here rather than in the tests because they are facts
    about the route, not about the check: the login *page* is the one GET that
    answers everyone (``public``); a login attempt with a bad password answers
    401 rather than redirecting (``no_session_status``); only the moderation
    actions write a trail row (``audit_delta``); issuing a code answers in place
    because the code is shown once (``success_status``); and a route that ends
    the session cannot be asked twice (``closes_session``).
    """

    method: str
    pattern: str
    path: str
    data: dict[str, str] = field(default_factory=dict)
    public: bool = False
    no_session_data: dict[str, str] | None = None
    no_session_status: int = 303
    success_status: int = 303
    audit_delta: int = 1
    closes_session: bool = False


CASES: tuple[Case, ...] = (
    # Signing in and out.
    Case("GET", "/admin/login", "/admin/login", public=True),
    Case(
        "POST",
        "/admin/login",
        "/admin/login",
        {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        no_session_data={"username": ADMIN_USERNAME, "password": WRONG_PASSWORD},
        no_session_status=401,
        audit_delta=0,
    ),
    Case(
        "POST",
        "/admin/logout",
        "/admin/logout",
        audit_delta=0,
        closes_session=True,
    ),
    # The panel and the invite codes.
    Case("GET", "/admin", "/admin"),
    Case("GET", "/admin/invites", "/admin/invites"),
    Case(
        "POST",
        "/admin/invites",
        "/admin/invites",
        {"ttl_days": "7"},
        success_status=200,
        audit_delta=0,
    ),
    # The three queues, and the offers to help build the site.
    Case("GET", "/admin/reports", "/admin/reports"),
    Case("GET", "/admin/pii", "/admin/pii"),
    Case("GET", "/admin/blocked", "/admin/blocked"),
    Case("GET", "/admin/audit", "/admin/audit"),
    Case("GET", "/admin/dev-applications", "/admin/dev-applications"),
    # One object, and the verdict on it.
    Case(
        "GET",
        "/admin/content/{target_type}/{target_id}",
        "/admin/content/post/{post}",
    ),
    Case(
        "POST",
        "/admin/content/{target_type}/{target_id}/decide",
        "/admin/content/post/{post}/decide",
        {"decision": ACTION_HIDE},
    ),
    # One complaint and one PII flag, each closed on its own.
    Case(
        "POST", "/admin/reports/{report_id}/dismiss", "/admin/reports/{report}/dismiss"
    ),
    Case(
        "POST", "/admin/pii-flags/{flag_id}/resolve", "/admin/pii-flags/{flag}/resolve"
    ),
    Case(
        "POST", "/admin/pii-flags/{flag_id}/dismiss", "/admin/pii-flags/{flag}/dismiss"
    ),
    # A student, and their ban.
    Case("GET", "/admin/students/{student_id}", "/admin/students/{author}"),
    Case(
        "POST", "/admin/students/{student_id}/ban", "/admin/students/{ban_student}/ban"
    ),
    Case(
        "POST",
        "/admin/students/{student_id}/unban",
        "/admin/students/{banned_student}/unban",
    ),
)

GET_CASES = tuple(case for case in CASES if case.method == "GET")
POST_CASES = tuple(case for case in CASES if case.method == "POST")


def _case_id(case: Case) -> str:
    return f"{case.method} {case.pattern}"


get_route = pytest.mark.parametrize("case", GET_CASES, ids=_case_id)
post_route = pytest.mark.parametrize("case", POST_CASES, ids=_case_id)


def _pattern(path: str) -> str:
    """A route path with every parameter name replaced by ``{}``."""
    return _PATH_PARAM_RE.sub("{}", path)


def _registered_routes() -> set[tuple[str, str]]:
    """Every ``(method, pattern)`` the two admin routers actually serve.

    ``HEAD`` is dropped because Starlette adds it to every GET route, so it says
    nothing about an admin screen being exposed or protected.
    """
    routes: set[tuple[str, str]] = set()
    for router in (admin_router.router, moderation_router.router):
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods - {"HEAD"}:
                routes.add((method, _pattern(route.path)))
    return routes


@pytest.fixture()
def subjects(session_factory) -> dict[str, int]:
    """One object per write route, so no case can disturb another's target.

    Sharing a single post between the verdict, the complaint and the flag cases
    would make them order-dependent: a verdict on an object closes the open
    reports on it, so dismissing that complaint afterwards would no longer be a
    change at all. Each case gets its own row instead.
    """
    with session_factory() as db:
        author = Student(alias="Ученик-автор")
        reporter = Student(alias="Ученик-заявитель")
        ban_target = Student(alias="Ученик-под-баном")
        banned_student = Student(alias="Ученик-забаненный", is_banned=True)
        db.add_all([author, reporter, ban_target, banned_student])
        db.flush()

        post = Post(
            author_id=author.id,
            display_alias=author.alias,
            is_anonymous=False,
            body="Пост, о котором спорят",
            status=CONTENT_STATUS_VISIBLE,
        )
        report_post = Post(
            author_id=author.id,
            display_alias=author.alias,
            is_anonymous=False,
            body="Пост, на который пожаловались",
            status=CONTENT_STATUS_VISIBLE,
        )
        flag_post = Post(
            author_id=author.id,
            display_alias=author.alias,
            is_anonymous=False,
            body="Пост с телефоном в тексте",
            status=CONTENT_STATUS_VISIBLE,
        )
        db.add_all([post, report_post, flag_post])
        db.flush()

        report = reports_repo.create_report(
            db,
            reporter_id=reporter.id,
            target_type=TARGET_POST,
            target_id=report_post.id,
            reason_code="spam",
            comment=None,
        )
        flag = pii_flags_repo.create_flag(
            db,
            target_type=TARGET_POST,
            target_id=flag_post.id,
            rule_code="phone",
            snippet="+7 ...",
        )
        db.commit()

        return {
            "post": post.id,
            "author": author.id,
            "ban_student": ban_target.id,
            "banned_student": banned_student.id,
            "report": report.id,
            "flag": flag.id,
        }


def _csrf_from(client) -> str:
    """A token the site issued, taken from the form the login page renders."""
    response = client.get("/admin/login")
    assert response.status_code == 200, response.text
    return extract_csrf(response.text)


def _audit_count(session_factory) -> int:
    with session_factory() as db:
        return audit_repo.count_all(db)


def _post_status(session_factory, post_id: int) -> str:
    with session_factory() as db:
        return db.get(Post, post_id).status


def _flag_status(session_factory, flag_id: int) -> str:
    with session_factory() as db:
        return db.get(PiiFlag, flag_id).status


# --- The table itself ---------------------------------------------------------


def test_the_table_covers_every_registered_admin_route():
    """A new admin route must be listed here, with its expectations, or fail."""
    listed = {(case.method, _pattern(case.pattern)) for case in CASES}
    assert listed == _registered_routes()


# --- Reading ------------------------------------------------------------------


@get_route
def test_an_anonymous_get_is_sent_to_the_login_page(case, client, subjects):
    response = client.get(case.path.format(**subjects), follow_redirects=False)

    if case.public:
        assert response.status_code == 200
        return
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


@get_route
def test_an_admin_get_renders_its_page(case, admin, subjects):
    response = admin.client.get(case.path.format(**subjects))

    assert response.status_code == 200, response.text


# --- Writing ------------------------------------------------------------------


@post_route
def test_a_post_without_a_csrf_token_is_refused(case, client, subjects):
    """The token is checked first, so this holds before any session is looked at.

    The correct password is deliberately sent to the login route here: even that
    must not open a session when the request carries no token.
    """
    response = client.post(
        case.path.format(**subjects), data=case.data, follow_redirects=False
    )

    assert response.status_code == 403
    assert client.get("/admin", follow_redirects=False).status_code == 303


@post_route
def test_a_post_without_a_session_is_sent_to_the_login_page(
    case, client, subjects, session_factory
):
    """A token the site itself issued is not enough; the action needs a session."""
    before = _audit_count(session_factory)
    payload = dict(case.no_session_data or case.data)

    response = client.post(
        case.path.format(**subjects),
        data={**payload, settings.CSRF_FORM_FIELD: _csrf_from(client)},
        follow_redirects=False,
    )

    assert response.status_code == case.no_session_status
    if case.no_session_status == 303:
        assert response.headers["location"] == "/admin/login"
    # The refusal must land before anything is written.
    assert _audit_count(session_factory) == before


@post_route
def test_an_admins_post_is_prg_and_writes_exactly_one_row(
    case, admin, subjects, session_factory
):
    """A success answers as the route promises, and a repeat writes nothing.

    The one route that answers in place instead of redirecting is in the table
    with its ``success_status``: an issued code has to be shown once, so a
    redirect would lose it. The repeat check is the point of the sweep -- the
    second identical request must add no row to the trail.
    """
    before = _audit_count(session_factory)

    response = admin.post(case.path.format(**subjects), "/admin", case.data)

    assert response.status_code == case.success_status
    assert _audit_count(session_factory) - before == case.audit_delta

    if case.closes_session:
        return

    repeat = admin.post(case.path.format(**subjects), "/admin", case.data)

    assert _audit_count(session_factory) - before == case.audit_delta
    assert repeat.status_code in (200, 303)


# --- The state the same requests leave behind ---------------------------------


def test_a_verdict_hides_the_object_and_a_repeat_leaves_it_hidden(
    admin, subjects, session_factory
):
    """The generic sweep counts trail rows; this pins the state change itself.

    Handing down a verdict the object already has is a no-op rather than a
    refusal: the moderator is sent back to the hub and the trail stays as it
    was, so a double-clicked button cannot look like a second decision.
    """
    post_id = subjects["post"]
    path = f"/admin/content/post/{post_id}/decide"
    before = _audit_count(session_factory)

    assert admin.post(path, "/admin", {"decision": ACTION_HIDE}).status_code == 303
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_HIDDEN
    assert _audit_count(session_factory) - before == 1

    assert admin.post(path, "/admin", {"decision": ACTION_HIDE}).status_code == 303
    assert _post_status(session_factory, post_id) == CONTENT_STATUS_HIDDEN
    assert _audit_count(session_factory) - before == 1


def test_an_already_closed_flag_cannot_be_closed_again(
    admin, subjects, session_factory
):
    """Resolving a flag twice is refused with the object's card, not a redirect."""
    flag_id = subjects["flag"]
    path = f"/admin/pii-flags/{flag_id}/resolve"

    assert admin.post(path, "/admin", {}).status_code == 303
    assert _flag_status(session_factory, flag_id) == PII_FLAG_STATUS_RESOLVED

    assert admin.post(path, "/admin", {}).status_code == 200
    assert _flag_status(session_factory, flag_id) == PII_FLAG_STATUS_RESOLVED
