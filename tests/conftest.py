"""Shared test fixtures: an isolated database, an HTTP client and a logged-in student.

The application builds its engine once at import time from ``DATABASE_URL``, so
the tests do not reuse it: every test gets its own temporary SQLite file and the
``get_db`` dependency is overridden to hand out sessions bound to it. Nothing
here ever touches the development ``school.db``.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# The tests live in ``tests/`` without a package marker, so make the project
# root importable when pytest is invoked from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401,E402  (registers every table on Base.metadata)
from app.config import settings  # noqa: E402
from app.db.base import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.student import Student  # noqa: E402
from app.services import student_auth  # noqa: E402
from app.services.rate_limit import limiter  # noqa: E402

# The hidden field every form renders. Matching it in the page is how a test
# gets a token that goes with the CSRF cookie the same response set.
_CSRF_FIELD_RE = re.compile(r'name="csrf_token" value="([^"]*)"')


def extract_csrf(html: str) -> str:
    """Pull the CSRF token out of a rendered form (fails loudly if absent)."""
    match = _CSRF_FIELD_RE.search(html)
    assert match, "the page rendered no csrf_token field"
    return match.group(1)


@dataclass
class StudentSession:
    """A client already carrying a student session, plus that student's id."""

    client: TestClient
    student_id: int

    def csrf_for(self, page: str) -> str:
        """Visit ``page`` and return the token from the form on it.

        The visit is also what gets the CSRF cookie into the client's jar, so
        the returned token always matches the cookie that will be sent.
        """
        response = self.client.get(page)
        assert response.status_code == 200, response.text
        return extract_csrf(response.text)

    def post(self, url: str, page: str, data: dict, **kwargs):
        """POST ``data`` to ``url`` with a token taken from ``page``."""
        return self.client.post(
            url,
            data={**data, "csrf_token": self.csrf_for(page)},
            follow_redirects=False,
            **kwargs,
        )


@pytest.fixture()
def db_engine(tmp_path):
    """A fresh file-backed SQLite database with the full schema."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    """Session factory bound to the test database.

    ``expire_on_commit`` is off so a fixture can read an object's id after the
    session that made it has committed and closed.
    """
    return sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


@pytest.fixture()
def client(session_factory):
    """A TestClient wired to the test database, with the rate limiter reset."""

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    limiter.reset()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    limiter.reset()


@pytest.fixture()
def student(client, session_factory):
    """Create a student and return the client, already logged in as them."""
    with session_factory() as db:
        student = Student(alias="Ученик-01")
        db.add(student)
        db.commit()
        token = student_auth.issue_student_session(student)
        student_id = student.id

    # The domain matters: http.cookiejar files a host-only cookie for
    # "testserver" under "testserver.local", and a cookie set without a domain
    # would live in a separate jar entry the server's own Set-Cookie can never
    # clear.
    client.cookies.set(settings.STUDENT_COOKIE_NAME, token, domain="testserver.local")
    return StudentSession(client=client, student_id=student_id)
